"""ORM-модели модуля «Парк БВС» (ПБС.ФТ.2, ФТ.4).

Парков несколько, каждый — самостоятельная именованная сущность со своей
локацией (расширение ПБС.ФТ.11 по запросу пользователя: прежде парк был один
и загрузка заменяла его целиком). Поэтому здесь нет ни признака «текущий», ни
уникального индекса по нему: задача ссылается на конкретный парк по
``tasks.fleet_id``, а не на глобально выбранный.

Локация парка (``location_lat``/``location_lon``) обязательна и вычисляется
сервисом из координат экземпляров при загрузке, а не вводится руками; поэтому
она NOT NULL, а загрузка файла без единой координаты отвергается.

Уникальность инвентарного номера намеренно НЕ вынесена в ограничение БД:
сервис сохраняет строки с дублями и помечает их невалидными (ПБС.ФТ.2), то
есть это правило валидации, а не правило хранения.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uav_planner.db.base import Base


class FleetUpload(Base):
    __tablename__ = "fleet_uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    location_lat: Mapped[float] = mapped_column(Float, nullable=False)
    location_lon: Mapped[float] = mapped_column(Float, nullable=False)
    location_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ready_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    instances: Mapped[list["FleetInstance"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        order_by="FleetInstance.ordinal",
        lazy="selectin",
    )
    issues: Mapped[list["FleetIssue"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        order_by="FleetIssue.ordinal",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("status IN ('Корректна', 'Содержит ошибки')", name="status_values"),
    )


class FleetInstance(Base):
    __tablename__ = "fleet_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fleet_uploads.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory_number: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_launch_site: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Фактические координаты экземпляра: разные борта одного парка могут стоять
    # на разных площадках. Необязательны — из них выводится точка взлёта, а при
    # их отсутствии план берёт ближайшую ВПП обстановки.
    location_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    upload: Mapped[FleetUpload] = relationship(back_populates="instances")

    __table_args__ = (
        UniqueConstraint("upload_id", "ordinal"),
        # ПБС.ФТ.4: кандидатами на расчет становятся только готовые и валидные.
        Index(
            "ix_fleet_instances_eligible",
            "upload_id",
            postgresql_where="is_valid AND status = 'Готов'",
        ),
    )


class FleetIssue(Base):
    __tablename__ = "fleet_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fleet_uploads.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    upload: Mapped[FleetUpload] = relationship(back_populates="issues")

    __table_args__ = (UniqueConstraint("upload_id", "ordinal"),)
