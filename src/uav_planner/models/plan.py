"""ORM-модели модуля «Планирование» (ПЛН.ФТ.3-10) и статусов плана (ЭКС.ФТ.5).

ПЛН.ФТ.4: повторный расчет создает новую версию, предыдущие не меняются и не
удаляются. Номер версии уникален в пределах задачи — это ограничение БД, а не
соглашение: оно страхует от гонки при одновременном расчете.

Жизненный цикл статуса (ЭКС.ФТ.5): Черновик → Проверен → Подтвержден →
Выгружен. Переходы делаются условными ``UPDATE ... WHERE status = :ожидаемый``,
а не чтением с последующей записью: ЭКС.ФТ.9 требует, чтобы при одновременном
подтверждении выиграл первый, а второй получил отказ с актуальным статусом.

Маршруты и галсы — единственная геометрия, которую порождаем мы сами, поэтому
здесь, в отличие от обстановки, авторитетна именно геометрическая колонка, а
JSONB-копии нет. Читать ее нужно через shapely (``db.geo.from_db_geojson``), а
не через ``ST_AsGeoJSON``, который обрезает координаты до 9 знаков.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uav_planner.db.base import Base

PLAN_STATUSES = ("Черновик", "Проверен", "Подтвержден", "Выгружен")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    criterion_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    criterion_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    j1_s: Mapped[float] = mapped_column(Float, nullable=False)
    j2_s: Mapped[float] = mapped_column(Float, nullable=False)
    is_optimal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uav_model: Mapped[str] = mapped_column(Text, nullable=False)
    sortie_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # --- жизненный цикл плана, ЭКС.ФТ.5 -----------------------------------
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Черновик")
    # Аутентификации по ТЗ нет (ответы экспертов, п. 21), поэтому «кто» — это
    # имя из заголовка X-User-Name. Нужно ровно для сообщения ЭКС.ФТ.9
    # «План уже подтвержден пользователем (ФИО)».
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Заявленные параметры расчета — независимая проверка безопасности сверяет
    # маршруты именно с ними, не заглядывая внутрь решателя.
    model_key: Mapped[str] = mapped_column(Text, nullable=False)
    camera_key: Mapped[str] = mapped_column(Text, nullable=False)
    height_m: Mapped[float] = mapped_column(Float, nullable=False)
    swath_m: Mapped[float] = mapped_column(Float, nullable=False)
    cruise_speed_mps: Mapped[float] = mapped_column(Float, nullable=False)
    budget_s: Mapped[float] = mapped_column(Float, nullable=False)

    sorties: Mapped[list["PlanSortie"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="(PlanSortie.uav_id, PlanSortie.sortie_index)",
        lazy="selectin",
    )

    exports: Mapped[list["ExportArtifact"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin",
        order_by="ExportArtifact.created_at",
    )

    __table_args__ = (
        UniqueConstraint("task_id", "version"),
        CheckConstraint(
            "status IN ('Черновик', 'Проверен', 'Подтвержден', 'Выгружен')",
            name="status_values",
        ),
        # Подтверждение и выгрузка не бывают без того, кто их сделал.
        CheckConstraint(
            "(status IN ('Подтвержден', 'Выгружен')) = (confirmed_at IS NOT NULL)",
            name="confirmed_consistency",
        ),
        Index("ix_plans_task_id_version", "task_id", "version"),
    )


class PlanSortie(Base):
    __tablename__ = "plan_sorties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    uav_id: Mapped[str] = mapped_column(Text, nullable=False)
    sortie_index: Mapped[int] = mapped_column(Integer, nullable=False)
    takeoff_site: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_site: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    flight_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    # Маршрут целиком (галсы + перелеты) и отдельно только галсы: проверка
    # покрытия обязана смотреть на галсы, а не на весь трек.
    route_geom = mapped_column(Geometry("LINESTRING", srid=4326), nullable=False)
    survey_tracks_geom = mapped_column(Geometry("MULTILINESTRING", srid=4326), nullable=False)

    plan: Mapped[Plan] = relationship(back_populates="sorties")

    __table_args__ = (
        UniqueConstraint("plan_id", "uav_id", "sortie_index"),
        CheckConstraint("end_utc >= start_utc", name="time_order"),
    )


class ExportArtifact(Base):
    """Журнал выгрузок (ЭКС.ФТ.7).

    Сами файлы не хранятся: KML и GeoJSON — чистая функция от плана, который
    неизменяем после подтверждения (ЭКС.ФТ.3), поэтому повторная генерация
    даст байт в байт то же самое. В таблице только факт выгрузки — он нужен
    статусу «Выгружен» и разбору «что именно скачали и когда».
    """

    __tablename__ = "export_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    uav_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # NULL — архив по всей группе
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    plan: Mapped[Plan] = relationship(back_populates="exports")

    __table_args__ = (
        CheckConstraint("format IN ('kml', 'geojson', 'zip')", name="format_values"),
        Index("ix_export_artifacts_plan_id_created_at", "plan_id", "created_at"),
    )
