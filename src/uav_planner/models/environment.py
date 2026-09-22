"""ORM-модели модуля «Обстановка» (ОБС.ФТ.2-9).

Геометрия хранится дважды и это не дублирование, а осознанное решение:

* ``EnvironmentFeature.feature`` — сырой GeoJSON Feature ровно в том виде, в
  каком его вернет API (вместе с дописанными сервисом ``properties._valid`` и
  ``_buffer_geojson`` и любыми «лишними» свойствами из файла оператора). Это
  источник истины для ответа;
* ``geom``/``buffer_geom`` — производная индексируемая копия для
  пространственных запросов и проверок на уровне БД.

Круг через PostGIS нормализует геометрию (теряет обертку Feature и
негеометрические члены, может переупорядочить кольца, схлопнуть одночастный
MultiPolygon), поэтому отдавать наружу нормализованную копию нельзя — фронтенд
и тесты ждут исходную.
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

LAYER_VALUES = ("launch_site", "airspace", "no_fly", "obstacle", "reserve_site")
ENVIRONMENT_STATUSES = ("Корректна", "Содержит ошибки")


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Счетчики объектов по слоям — денормализованный снимок на момент загрузки
    # (ОБС.ФТ.5: «агрегируется на момент сохранения»).
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    features: Mapped[list["EnvironmentFeature"]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
        order_by="EnvironmentFeature.feature_index",
        lazy="selectin",
    )
    issues: Mapped[list["EnvironmentIssue"]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
        order_by="EnvironmentIssue.ordinal",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('Корректна', 'Содержит ошибки')", name="status_values"
        ),
        Index("ix_environments_uploaded_at", "uploaded_at"),
    )


class EnvironmentFeature(Base):
    __tablename__ = "environment_features"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="CASCADE"), nullable=False
    )
    layer: Mapped[str] = mapped_column(String(32), nullable=False)
    # Позиция внутри своего слоя — часть контракта API (ValidationIssue.feature_index)
    # и порядка отрисовки, поэтому хранится явно, а не выводится из порядка строк.
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False)
    feature: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    geom = mapped_column(Geometry("GEOMETRY", srid=4326), nullable=True)
    buffer_geom = mapped_column(Geometry("GEOMETRY", srid=4326), nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Продвинутые из properties поля: нужны для ограничения целостности и
    # будущих запросов вида «какие обстановки покрывают высоту H».
    h_min_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    h_max_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    safety_buffer_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    environment: Mapped[Environment] = relationship(back_populates="features")

    __table_args__ = (
        UniqueConstraint("environment_id", "layer", "feature_index"),
        CheckConstraint(
            "layer IN ('launch_site', 'airspace', 'no_fly', 'obstacle', 'reserve_site')",
            name="layer_values",
        ),
        CheckConstraint(
            "h_min_m IS NULL OR h_max_m IS NULL OR h_min_m <= h_max_m", name="height_order"
        ),
        Index("ix_environment_features_environment_id_layer", "environment_id", "layer"),
    )


class EnvironmentIssue(Base):
    """Ошибки проверки обстановки (ОБС.ФТ.9) — хранятся вместе с обстановкой.

    ``ordinal`` сохраняет порядок, в котором ошибки были найдены: он входит в
    контракт (тесты и панель ошибок интерфейса опираются на первый элемент).
    """

    __tablename__ = "environment_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    layer: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    environment: Mapped[Environment] = relationship(back_populates="issues")

    __table_args__ = (
        UniqueConstraint("environment_id", "ordinal"),
    )
