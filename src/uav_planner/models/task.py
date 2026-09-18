"""ORM-модель модуля «Задача» (ЗАД.ФТ.2-5, ФТ.9-12).

``version`` — счетчик для оптимистичной блокировки (ЗАД.ФТ.12): обновление
идет условным ``UPDATE ... WHERE id = :id AND version = :expected``.

Область облета, как и объекты обстановки, хранится в двух видах: ``area``
(сырой GeoJSON, ровно то, что вернет API) и ``area_geom`` (индексируемая копия
для пространственных запросов).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uav_planner.db.base import Base
from .environment import Environment

TASK_STATUSES = ("Черновик", "Рассчитана", "Подтверждена")
SURVEY_TYPES = ("RGB", "мультиспектральная", "ИК", "LiDAR", "геофизическая")
CRITERION_MODES = ("Время", "Налет", "Компромисс")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False
    )
    survey_type: Mapped[str] = mapped_column(String(32), nullable=False)
    gsd_cm: Mapped[float] = mapped_column(Float, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    window_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    wind_speed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_cover_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    criterion_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    criterion_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    daylight_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    area: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    area_geom = mapped_column(Geometry("GEOMETRY", srid=4326), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ЗАД.ФТ.12 и ЭКС.ФТ.9 требуют показать в сообщении о конфликте, кто
    # изменил запись. Аутентификации по ТЗ нет, поэтому это просто имя из
    # заголовка X-User-Name.
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    # environment_name входит в ответ API (ЗАД.ФТ.6), но не хранится копией:
    # переименование обстановки должно быть видно в списке задач сразу.
    environment: Mapped[Environment] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "status IN ('Черновик', 'Рассчитана', 'Подтверждена')", name="status_values"
        ),
        CheckConstraint("gsd_cm > 0", name="gsd_positive"),
        CheckConstraint("criterion_alpha BETWEEN 0 AND 1", name="alpha_range"),
        CheckConstraint(
            "window_start IS NULL OR window_end IS NULL OR window_start <= window_end",
            name="window_order",
        ),
        Index("ix_tasks_environment_id", "environment_id"),
        Index("ix_tasks_updated_at", "updated_at"),
    )
