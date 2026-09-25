"""ORM-модели модуля «Проверка безопасности» (БЕЗ.ФТ.1-6).

Два поля вместо двух строк: прежняя реализация клала один и тот же отчет в
словарь под двумя ключами — под запрошенным планом и под пересчитанным
(автопересчет БЕЗ.ФТ.3 меняет версию плана). Здесь это ``plan_id`` (план, к
которому отчет относится) и ``requested_plan_id`` (план, о котором спросил
оператор). Так любой подсчет «сколько проверок было» не задваивается.

Счетчик автопересчетов ключуется парой (задача, версия задачи): правка задачи
поднимает версию и тем самым сбрасывает счетчик, как и требует БЕЗ.ФТ.3.
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
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uav_planner.db.base import Base

CHECK_NAMES = (
    "geozones", "allowed_space", "energy", "reachability",
    "coverage", "daylight", "separation",
)


class SafetyReport(Base):
    __tablename__ = "safety_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    requested_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    auto_recalc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    checks: Mapped[list["SafetyCheck"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="SafetyCheck.ordinal",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("status IN ('Пройдена', 'Есть нарушения')", name="status_values"),
        Index("ix_safety_reports_plan_id_created_at", "plan_id", "created_at"),
        Index(
            "ix_safety_reports_requested_plan_id_created_at",
            "requested_plan_id",
            "created_at",
        ),
    )


class SafetyCheck(Base):
    __tablename__ = "safety_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("safety_reports.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Объекты ViolationOut (id/message/lat/lon/ignored), а не строки: структуру
    # задаёт схема API и она будет меняться, поэтому jsonb без миграции типа.
    violations: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    # БЕЗ.ФТ.4: предлагаемые варианты решения (строки) — см. SafetyCheckOut.
    recommendations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    report: Mapped[SafetyReport] = relationship(back_populates="checks")

    __table_args__ = (
        UniqueConstraint("report_id", "ordinal"),
        UniqueConstraint("report_id", "name"),
    )


class SafetyAttemptCounter(Base):
    """Счетчик автоматических пересчетов на версию задачи (БЕЗ.ФТ.3, максимум 3)."""

    __tablename__ = "safety_attempt_counters"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("task_id", "task_version"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )
