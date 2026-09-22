"""ORM-модель фонового расчета — ПЛН.ФТ.3, ПЛН.ФТ.5 и БЕЗ.ФТ.3/ФТ.5.

Строка ``plan_jobs`` — единственный результат фоновой задачи, который видит
оператор: статус, стадия, процент, ссылка на готовый план или отчет. Поэтому
``task_ignore_result=True`` у Celery — дублировать это в Redis незачем.

Два поля-ссылки на план осознанно разведены: ``plan_id`` — вход (для
``kind='safety'`` это план, который попросили проверить), ``result_plan_id`` —
выход (рассчитанный план либо последняя версия после автопересчета БЕЗ.ФТ.3).
Складывать их в одну колонку значило бы терять, что именно спросил оператор.

Активная работа по задаче ровно одна: частичный уникальный индекс по
``task_id`` для статусов «В очереди»/«Выполняется». Это и есть защита от
двойного клика по «Рассчитать»; ``idempotency_key`` защищает от ретрая сети.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from uav_planner.db.base import Base

JOB_KINDS = ("plan", "safety")

# Ровно те строки, что перечисляет ПЛН.ФТ.5 (плюс «Отменен» — отмена расчета
# там же). Хранятся как text + CHECK, а не enum-типом БД.
STATUS_QUEUED = "В очереди"
STATUS_RUNNING = "Выполняется"
STATUS_DONE = "Завершен"
STATUS_TIMEOUT = "Остановлен по лимиту времени"
STATUS_FAILED = "Ошибка"
STATUS_CANCELLED = "Отменен"

JOB_STATUSES = (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_DONE,
    STATUS_TIMEOUT,
    STATUS_FAILED,
    STATUS_CANCELLED,
)
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

# Машиночитаемая причина — текст ошибки предназначен оператору и меняется,
# а фронтенду нужно отличать «задача невыполнима» (422) от сбоя.
ERROR_INFEASIBLE = "infeasible"
ERROR_TIMEOUT = "timeout"
ERROR_CANCELLED = "cancelled"
ERROR_INTERRUPTED = "interrupted"
ERROR_INTERNAL = "internal"
ERROR_NOT_FOUND = "not_found"

_STATUS_LIST = ", ".join(f"'{s}'" for s in JOB_STATUSES)
_ACTIVE_LIST = ", ".join(f"'{s}'" for s in ACTIVE_STATUSES)


class PlanJob(Base):
    __tablename__ = "plan_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_QUEUED)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=True
    )
    result_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    result_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("safety_reports.id", ondelete="SET NULL"), nullable=True
    )
    # Число автоматических пересчетов, израсходованных этой работой (БЕЗ.ФТ.3).
    auto_recalc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Заголовок Idempotency-Key, если клиент его прислал; иначе уникальное
    # значение. Почему не sha256 параметров по умолчанию — см. README:
    # повторный расчет той же задачи обязан создавать новую версию (ПЛН.ФТ.4).
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Обновляется тем же запросом, что и прогресс: по нему видно, что воркер
    # с работой погиб (рестарт контейнера), и строка не висит «Выполняется».
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="status_values"),
        CheckConstraint("kind IN ('plan', 'safety')", name="kind_values"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_range"),
        Index("uq_plan_jobs_idempotency_key", "idempotency_key", unique=True),
        Index(
            "uq_plan_jobs_active_task",
            "task_id",
            unique=True,
            postgresql_where=f"status IN ({_ACTIVE_LIST})",
        ),
        Index("ix_plan_jobs_task_id_queued_at", "task_id", "queued_at"),
    )
