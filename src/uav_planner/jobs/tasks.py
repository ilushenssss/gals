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

Здесь же живет janitor (``sweep_stale_jobs``) — периодическая задача, которую
ставит сервис ``beat``. Она добивает работы, чей воркер погиб вместе с
контейнером; раньше это делал ленивый сторож в пути чтения.

Идентификаторы работы и задачи кладутся в контекст журнала (``log_context``) до
первой записи, поэтому все строки одного расчета — включая сделанные сервисами
и репозиториями — выбираются из общего потока по ``job_id``.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from billiard.exceptions import SoftTimeLimitExceeded as BilliardSoftTimeLimit
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import update

from uav_planner.db.session import session_scope, short_session_scope
from uav_planner.domain.errors import PlanInfeasibleError
from uav_planner.logging_setup import log_context
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

from .celery_app import SWEEP_STALE_JOBS_TASK, celery_app
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


def _cancel_requested(job_id: str) -> bool:
    with short_session_scope() as session:
        row = session.get(PlanJob, uuid.UUID(job_id))
        return bool(row and row.cancel_requested)


def _finish(job_id: str, status: str, **values) -> None:
    _update_job(job_id, status=status, finished_at=datetime.now(timezone.utc), **values)


def _run(job_id: str, celery_task_id: str | None, body) -> None:
    """Общий каркас работы: контекст журнала, статусы, лимит, отмена, ошибки.

    ``body(progress, fields)`` обязан вернуть словарь полей результата для
    строки работы (``result_plan_id`` и/или ``result_report_id``); ``fields`` —
    уже прочитанные идентификаторы работы, чтобы каждая задача не читала их
    заново.
    """
    with log_context(job_id=job_id):
        try:
            fields = _load_job_fields(job_id)
        except KeyError as exc:
            # Строку работы удалили, пока сообщение лежало в очереди.
            log.warning("строка работы не найдена — выполнять нечего")
            _finish(job_id, STATUS_FAILED, error_code=ERROR_NOT_FOUND,
                    error=f"объект не найден: {exc}")
            return

        with log_context(task_id=fields["task_id"], kind=fields["kind"]):
            _run_body(job_id, celery_task_id, body, fields)


def _run_body(job_id: str, celery_task_id: str | None, body, fields: dict) -> None:
    if not _claim(job_id, celery_task_id):
        log.info("работа снята до старта — отменена или уже обработана")
        _ensure_cancelled(job_id)
        return

    log.info("расчет начат")
    started = time.monotonic()
    progress = ProgressReporter(job_id)
    try:
        with session_scope():
            result = body(progress, fields)
    except JobCancelled:
        # Штатный выход по флагу отмены (ПЛН.ФТ.5), не ошибка.
        _outcome(job_id, STATUS_CANCELLED, started, progress=0, error_code=ERROR_CANCELLED,
                 error="расчет отменен оператором", stage=None)
    except _SOFT_LIMIT:
        if _cancel_requested(job_id):
            # Тот же сигнал шлет escalate_cancel, когда кооперативная отмена не
            # успела за CANCEL_GRACE_SECONDS: оператор нажал «Отменить», и
            # исход — отмена, а не лимит времени.
            _outcome(job_id, STATUS_CANCELLED, started, progress=0, error_code=ERROR_CANCELLED,
                     error="расчет отменен оператором", stage=None)
            return
        # ПЛН.ФТ.3. Лучшее найденное решение сохранить пока нечего: текущий
        # конвейер эвристик не имеет промежуточного допустимого плана — он
        # появится вместе с решателем OR-Tools, который умеет отдавать
        # incumbent по time_limit. Остановка при этом честная и наблюдаемая.
        _outcome(job_id, STATUS_TIMEOUT, started, error_code=ERROR_TIMEOUT,
                 error="расчет остановлен по лимиту времени; оптимальность не гарантируется")
    except PlanInfeasibleError as exc:
        # ПЛН.ФТ.10: задача невыполнима — это результат расчета, а не сбой.
        _outcome(job_id, STATUS_FAILED, started, error_code=ERROR_INFEASIBLE, error=str(exc))
    except KeyError as exc:
        # Задачу или план удалили, пока работа стояла в очереди.
        _outcome(job_id, STATUS_FAILED, started, error_code=ERROR_NOT_FOUND,
                 error=f"объект не найден: {exc}")
    except Exception as exc:  # noqa: BLE001 — исход обязан попасть в строку работы
        log.exception("расчет завершился необработанной ошибкой")
        _outcome(job_id, STATUS_FAILED, started, error_code=ERROR_INTERNAL, error=str(exc))
    else:
        _outcome(job_id, STATUS_DONE, started, progress=100, stage="Завершен", **result)


def _outcome(job_id: str, status: str, started: float, **values) -> None:
    """Записать исход работы и одной строкой сказать о нем в журнал.

    Уровень зависит от исхода: «Завершен» и «Отменен» — штатные события,
    остальное требует внимания. Длительность здесь важнее, чем кажется: по ней
    видно, приблизился ли расчет к лимиту ПЛН.ФТ.3, до того как в него упрется.
    """
    _finish(job_id, status, **values)
    level = logging.INFO if status in (STATUS_DONE, STATUS_CANCELLED) else logging.WARNING
    log.log(
        level,
        "расчет завершен",
        extra={
            "job_status": status,
            "duration_s": round(time.monotonic() - started, 3),
            "error_code": values.get("error_code"),
        },
    )


@celery_app.task(name="gals.run_plan_job", bind=True)
def run_plan_job(self, job_id: str) -> None:
    """Расчет плана (ПЛН.ФТ.5)."""
    from uav_planner.services import plan_service

    def body(progress: ProgressReporter, fields: dict) -> dict:
        summary = plan_service.create_plan(fields["task_id"], progress=progress)
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

    def body(progress: ProgressReporter, fields: dict) -> dict:
        report = safety_service.check_plan(fields["plan_id"], progress=progress)
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
    ``_claim``.
    """
    with short_session_scope() as session:
        row = session.get(PlanJob, uuid.UUID(job_id))
        if row is None:
            raise KeyError(f"работа {job_id} не найдена")
        return {
            "task_id": str(row.task_id),
            "plan_id": str(row.plan_id) if row.plan_id else None,
            "kind": row.kind,
        }


@celery_app.task(name=SWEEP_STALE_JOBS_TASK)
def sweep_stale_jobs() -> int:
    """Janitor: перевести в «Ошибка» работы, чей воркер погиб (ПЛН.ФТ.5).

    Ставится по расписанию сервисом ``beat``, выполняется обычным воркером.
    Отдельный процесс вместо прежнего ленивого сторожа в пути чтения: исход
    работы не должен зависеть от того, заглянет ли кто-нибудь в нее, а
    получасовой расчет оператор вполне может оставить и уйти.
    """
    from uav_planner.services import job_service

    with session_scope():
        return len(job_service.sweep_stale())
