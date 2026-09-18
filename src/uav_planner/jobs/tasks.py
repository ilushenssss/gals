"""Задачи фонового расчета — ПЛН.ФТ.3/ФТ.5 и БЕЗ.ФТ.3/ФТ.5.

Обе задачи устроены одинаково: перевести строку ``plan_jobs`` в «Выполняется»,
позвать тот же сервис, что зовет синхронный путь, и записать исход. Вся
разница между фоном и синхронным вызовом — ``ProgressReporter``, который
сервисам передается необязательным аргументом. Математика не тронута.

Границы транзакций здесь важнее обычного:

* тело работы идет в ``session_scope()`` — одна транзакция на расчет, чтобы
  вставка плана и ``mark_calculated`` не разъехались (иначе падение между ними
  оставило бы план, у задачи которого статус «Черновик»);
* смена статуса работы («Выполняется», «Завершен», «Ошибка») пишется
  **отдельной короткой транзакцией** — ее видно снаружи сразу, а не после
  коммита получасового расчета;
* поэтому же исход записывается в ``finally``-подобной ветке уже после выхода
  из ``session_scope``: сначала откатывается или коммитится расчет, потом
  фиксируется его исход.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from billiard.exceptions import SoftTimeLimitExceeded as BilliardSoftTimeLimit
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import update

from uav_planner.db.session import session_scope, short_session_scope
from uav_planner.domain.errors import PlanInfeasibleError
from uav_planner.models.job import (
    ACTIVE_STATUSES,
    ERROR_CANCELLED,
    ERROR_INFEASIBLE,
    ERROR_INTERNAL,
    ERROR_NOT_FOUND,
    ERROR_TIMEOUT,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_TIMEOUT,
    PlanJob,
)

from .celery_app import celery_app
from .progress import JobCancelled, ProgressReporter

log = logging.getLogger(__name__)

# Celery на разных пулах бросает разные классы одного и того же события.
_SOFT_LIMIT = (SoftTimeLimitExceeded, BilliardSoftTimeLimit)


def _update_job(job_id: str, **values) -> None:
    with short_session_scope() as session:
        session.execute(
            update(PlanJob).where(PlanJob.id == uuid.UUID(job_id)).values(**values)
        )


def _claim(job_id: str, celery_task_id: str | None) -> bool:
    """Условно перевести работу в «Выполняется». False — выполнять не надо.

    Условие обязательное, а не украшение: ``revoke`` без ``terminate`` — это
    широковещательное сообщение, которое доходит только до уже запущенных
    воркеров. Сообщение, отмененное при остановленном воркере, останется в
    очереди и будет доставлено после его запуска. Единственная надежная точка
    отсечения — строка работы: она уже «Отменен» (или у нее поднят флаг), и
    тогда расчет не начинается вовсе.
    """
    now = datetime.now(timezone.utc)
    with short_session_scope() as session:
        claimed = session.execute(
            update(PlanJob)
            .where(
                PlanJob.id == uuid.UUID(job_id),
                PlanJob.status.in_(ACTIVE_STATUSES),
                PlanJob.cancel_requested.is_(False),
            )
            .values(
                status=STATUS_RUNNING,
                started_at=now,
                heartbeat_at=now,
                celery_task_id=celery_task_id,
                error=None,
                error_code=None,
            )
            .returning(PlanJob.id)
        ).scalar_one_or_none()
    return claimed is not None


def _finish(job_id: str, status: str, **values) -> None:
    _update_job(job_id, status=status, finished_at=datetime.now(timezone.utc), **values)


def _run(job_id: str, celery_task_id: str | None, body) -> None:
    """Общий каркас работы: статусы, лимит времени, отмена, ошибки.

    ``body(progress)`` обязан вернуть словарь полей результата для строки
    работы (``result_plan_id`` и/или ``result_report_id``).
    """
    if not _claim(job_id, celery_task_id):
        log.info("работа %s снята до старта (отменена или уже обработана)", job_id)
        _ensure_cancelled(job_id)
        return

    progress = ProgressReporter(job_id)
    try:
        with session_scope():
            result = body(progress)
    except JobCancelled:
        # Штатный выход по флагу отмены (ПЛН.ФТ.5), не ошибка.
        _finish(job_id, STATUS_CANCELLED, progress=0, error_code=ERROR_CANCELLED,
                error="расчет отменен оператором", stage=None)
    except _SOFT_LIMIT:
        # ПЛН.ФТ.3. Лучшее найденное решение сохранить пока нечего: текущий
        # конвейер эвристик не имеет промежуточного допустимого плана — он
        # появится вместе с решателем OR-Tools, который умеет отдавать
        # incumbent по time_limit. Остановка при этом честная и наблюдаемая.
        _finish(job_id, STATUS_TIMEOUT, error_code=ERROR_TIMEOUT,
                error="расчет остановлен по лимиту времени; оптимальность не гарантируется")
    except PlanInfeasibleError as exc:
        # ПЛН.ФТ.10: задача невыполнима — это результат расчета, а не сбой.
        _finish(job_id, STATUS_FAILED, error_code=ERROR_INFEASIBLE, error=str(exc))
    except KeyError as exc:
        # Задачу или план удалили, пока работа стояла в очереди.
        _finish(job_id, STATUS_FAILED, error_code=ERROR_NOT_FOUND,
                error=f"объект не найден: {exc}")
    except Exception as exc:  # noqa: BLE001 — исход обязан попасть в строку работы
        log.exception("работа %s завершилась ошибкой", job_id)
        _finish(job_id, STATUS_FAILED, error_code=ERROR_INTERNAL, error=str(exc))
    else:
        _finish(job_id, STATUS_DONE, progress=100, stage="Завершен", **result)


@celery_app.task(name="gals.run_plan_job", bind=True)
def run_plan_job(self, job_id: str) -> None:
    """Расчет плана (ПЛН.ФТ.5)."""
    from uav_planner.services import plan_service

    def body(progress: ProgressReporter) -> dict:
        job = _load_job_fields(job_id)
        summary = plan_service.create_plan(str(job["task_id"]), progress=progress)
        return {"result_plan_id": uuid.UUID(summary.id)}

    _run(job_id, getattr(self.request, "id", None), body)


@celery_app.task(name="gals.run_safety_job", bind=True)
def run_safety_job(self, job_id: str) -> None:
    """Проверка безопасности с автопересчетом (БЕЗ.ФТ.1, ФТ.3, ФТ.5).

    Цикл «проверка → пересчет → проверка» живет здесь, а не в HTTP-запросе, —
    именно это делает наблюдаемым статус «В процессе автоматического
    пересчета» (БЕЗ.ФТ.5), который в синхронном варианте невозможен.
    """
    from uav_planner.services import safety_service

    def body(progress: ProgressReporter) -> dict:
        job = _load_job_fields(job_id)
        report = safety_service.check_plan(str(job["plan_id"]), progress=progress)
        return {
            "result_plan_id": uuid.UUID(report.plan_id),
            "result_report_id": uuid.UUID(report.id),
            "auto_recalc_count": report.auto_recalc_count,
        }

    _run(job_id, getattr(self.request, "id", None), body)


@celery_app.task(name="gals.escalate_cancel")
def escalate_cancel(job_id: str, celery_task_id: str) -> None:
    """Жесткая остановка, если кооперативная отмена не сработала.

    Ставится с задержкой ``CANCEL_GRACE_SECONDS`` в момент нажатия «Отменить».
    Если к этому моменту работа уже ушла из «Выполняется» — ничего не делает;
    иначе снимает задачу сигналом, и ``finally`` внутри Celery все равно
    отрабатывает.
    """
    from .control import revoke_task

    with short_session_scope() as session:
        row = session.get(PlanJob, uuid.UUID(job_id))
        if row is None or row.status != STATUS_RUNNING or not row.cancel_requested:
            return
    revoke_task(celery_task_id, terminate=True)


def _ensure_cancelled(job_id: str) -> None:
    """Дописать исход работе, снятой до старта, если его еще нет.

    Отмену из очереди обычно записывает сам эндпоинт отмены; сюда попадает
    случай, когда воркер в этот момент был остановлен и запись не произошла.
    """
    with short_session_scope() as session:
        session.execute(
            update(PlanJob)
            .where(PlanJob.id == uuid.UUID(job_id), PlanJob.status.in_(ACTIVE_STATUSES))
            .values(
                status=STATUS_CANCELLED,
                progress=0,
                error_code=ERROR_CANCELLED,
                error="расчет отменен оператором",
                finished_at=datetime.now(timezone.utc),
            )
        )


def _load_job_fields(job_id: str) -> dict:
    """Идентификаторы работы читаются своей короткой транзакцией.

    Читать их из транзакции расчета нельзя: она открывается позже и живет до
    конца работы, а строку ``plan_jobs`` к этому моменту уже успел изменить
    ``_mark_started``.
    """
    with short_session_scope() as session:
        row = session.get(PlanJob, uuid.UUID(job_id))
        if row is None:
            raise KeyError(f"работа {job_id} не найдена")
        return {"task_id": row.task_id, "plan_id": row.plan_id, "kind": row.kind}
