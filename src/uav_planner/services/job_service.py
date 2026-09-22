"""Постановка в очередь, гибридное ожидание и отмена фонового расчета.

**Гибридное ожидание вместо развилки в API.** ``POST /api/plans`` и
``POST /api/safety-checks`` ставят работу в очередь и до ``wait_s`` секунд
опрашивают ее строку. Успел — отдается прежний ``200`` с тем же телом, что и
раньше; не успел — ``202`` и объект работы. Сцены тестового масштаба считаются
за миллисекунды, поэтому все существующие тесты API остаются зелеными без
правки ассертов, а в проде ``wait_s=0`` делает фронт честным поллером.

**Почему ключ идемпотентности по умолчанию не хеш параметров.** План обертки
предлагал ``sha256(kind|task_id|task_version|params)``, но это прямо
противоречит ПЛН.ФТ.4: повторный расчет той же неизмененной задачи обязан
создавать новую версию плана (и существующий тест на это есть). Поэтому по
умолчанию ключ уникален, а от двойного клика защищает частичный уникальный
индекс «одна активная работа на задачу»: второй клик получает ту же работу,
а осознанный повторный расчет после завершения первого — новую. Клиент,
которому нужна строгая идемпотентность ретраев, присылает ``Idempotency-Key``.

**Опрос — своими короткими транзакциями.** Сессия HTTP-запроса живет в своем
снимке и не увидит коммитов воркера; кроме того, держать транзакцию открытой,
пока мы спим, нельзя.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.exc import IntegrityError

from uav_planner import repositories
from uav_planner.api.schemas.job import JobInfo
from uav_planner.config import get_settings
from uav_planner.db.session import current_session, short_session_scope
from uav_planner.domain.errors import GalsError
from uav_planner.logging_setup import log_context
from uav_planner.models.job import (
    ERROR_CANCELLED,
    ERROR_INFEASIBLE,
    ERROR_NOT_FOUND,
    STATUS_CANCELLED,
    STATUS_QUEUED,
)

POLL_INTERVAL_S = 0.05

log = logging.getLogger(__name__)


class JobConflictError(GalsError):
    """По задаче уже идет расчет — второй запускать нельзя."""


def _enqueue(job: JobInfo) -> None:
    """Отправить работу в очередь.

    Строка ``plan_jobs`` обязана быть закоммичена до отправки: воркер — другой
    процесс с другим соединением, и незакоммиченную строку он не увидит, а
    сообщение в брокере окажется быстрее транзакции.
    """
    from uav_planner.jobs import tasks

    current_session().commit()
    task = tasks.run_plan_job if job.kind == "plan" else tasks.run_safety_job
    async_result = task.delay(job.id)
    task_id = getattr(async_result, "id", None)
    if task_id:
        repositories.jobs.set_celery_task_id(job.id, task_id)
        current_session().commit()


def submit(
    kind: str,
    task_id: str,
    *,
    plan_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[JobInfo, bool]:
    """Поставить работу в очередь. Второй элемент — True, если она новая.

    Не новая — это не ошибка: двойной клик по «Рассчитать» и ретрай сети
    должны давать одну работу, а не две.
    """
    key = idempotency_key or f"auto:{uuid.uuid4()}"

    if idempotency_key:
        existing = repositories.jobs.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing, False

    try:
        job = repositories.jobs.create(
            task_id=task_id, kind=kind, idempotency_key=key, plan_id=plan_id
        )
    except IntegrityError:
        # Гонка двух запросов на частичном уникальном индексе.
        current_session().rollback()
        job = None

    if job is None:
        active = repositories.jobs.find_active(task_id)
        if active is not None:
            return active, False
        by_key = repositories.jobs.find_by_idempotency_key(key)
        if by_key is not None:
            return by_key, False
        raise JobConflictError("не удалось поставить расчет в очередь — повторите попытку")

    _enqueue(job)
    with log_context(job_id=job.id, task_id=task_id):
        log.info("работа поставлена в очередь", extra={"kind": kind, "plan_id": plan_id})
    return job, True


def get_job(job_id: str) -> JobInfo:
    """Строка работы как есть — чтение ничего не чинит.

    Работу, чей контейнер перезапустили, переводит в «Ошибка» janitor
    (``sweep_stale`` ниже, по расписанию ``beat``), а не путь чтения. Раньше
    сторож был ленивым и жил здесь; это значило, что запись о смерти воркера
    появляется, только если кто-то заглянет, а два одновременных запроса пишут
    ее наперегонки. Цена переноса — работа считается осиротевшей не мгновенно,
    а с задержкой до одного тика janitor'а.
    """
    return repositories.jobs.get(job_id)


def list_jobs(task_id: str) -> list[JobInfo]:
    """Все работы задачи, свежие первыми — переподключение индикатора после F5."""
    return repositories.jobs.list_by_task_newest_first(task_id)


def sweep_stale() -> list[str]:
    """Janitor: добить работы, чей воркер погиб (ПЛН.ФТ.5, наблюдаемый исход).

    Вызывается периодической задачей ``gals.sweep_stale_jobs``. Отдельная
    строка журнала на каждую добитую работу — по ней потом видно, сколько
    расчетов унес перезапуск и чьи именно это были задачи.
    """
    job_ids = repositories.jobs.sweep_stale(get_settings().job_stale_after_seconds)
    for job_id in job_ids:
        with log_context(job_id=job_id):
            log.warning("работа прервана перезапуском сервиса — heartbeat устарел")
    return job_ids


def cancel(job_id: str) -> JobInfo:
    """Отмена расчета (ПЛН.ФТ.5) — кооперативно, с эскалацией по таймауту.

    Работу, еще не взятую воркером, снимаем сразу: ``revoke`` выбрасывает
    сообщение из очереди, и статус «Отменен» можно ставить здесь же. Идущую
    работу останавливает флаг, который воркер проверяет на каждой границе
    стадии; если через ``CANCEL_GRACE_SECONDS`` она все еще идет, отложенная
    задача ``escalate_cancel`` снимает ее сигналом.
    """
    from uav_planner.jobs import tasks
    from uav_planner.jobs.control import revoke_task

    job = get_job(job_id)
    if job.is_finished:
        return job

    log.info("запрошена отмена расчета", extra={"job_id": job_id, "job_status": job.status})
    repositories.jobs.request_cancel(job_id)
    celery_task_id = repositories.jobs.get_celery_task_id(job_id)
    current_session().commit()

    if job.status == STATUS_QUEUED and revoke_task(celery_task_id):
        # Сообщение выброшено из очереди — воркер его уже не возьмет, и статус
        # можно поставить здесь, не дожидаясь никого.
        repositories.jobs.finish(
            job_id, STATUS_CANCELLED, progress=0,
            error_code=ERROR_CANCELLED, error="расчет отменен оператором",
        )
        current_session().commit()
        return repositories.jobs.get(job_id)

    if celery_task_id:
        tasks.escalate_cancel.apply_async(
            (job_id, celery_task_id), countdown=get_settings().cancel_grace_seconds
        )
    return repositories.jobs.get(job_id)


def wait_for(job_id: str, wait_s: float) -> JobInfo:
    """Дождаться завершения работы, но не дольше ``wait_s``.

    Опрос, а не подписка на результат Celery: результат работы — строка в БД
    (``task_ignore_result=True``), и опрос ее же избавляет от второго источника
    правды. Интервал 50 мс важен для тестовых сцен, которые считаются быстрее
    любого разумного шага поллинга.
    """
    job = get_job(job_id)
    if wait_s <= 0 or job.is_finished:
        return job

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_S)
        with short_session_scope(bind=True):
            job = repositories.jobs.get(job_id)
        if job.is_finished:
            return job
    return job


def result_error(job: JobInfo) -> tuple[int, str] | None:
    """Исход работы как код HTTP и текст — общий для обоих синхронных путей."""
    if job.error_code == ERROR_INFEASIBLE:
        return 422, job.error or "задачу невозможно рассчитать"
    if job.error_code == ERROR_NOT_FOUND:
        return 404, job.error or "объект не найден"
    if not job.is_finished:
        return None
    if job.status in ("Завершен",):
        return None
    return 409, job.error or f"расчет завершился со статусом «{job.status}»"
