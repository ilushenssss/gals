"""Pydantic-схемы модуля «Парк БВС» — см. docs/trebovania/Парк_БВС.md.

Парков несколько, каждый — именованный список экземпляров БВС со своей
локацией (расширение ПБС.ФТ.11 по запросу пользователя: прежде парк был один
и повторная загрузка заменяла его целиком). Задача ссылается на конкретный
парк по ``TaskDetail.fleet_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

Status = Literal["Корректна", "Содержит ошибки"]


class FleetIssue(BaseModel):
    inventory_number: Optional[str] = None
    message: str


class FleetInstance(BaseModel):
    inventory_number: str
    model_key: str
    model_name: str
    base_launch_site: Optional[str] = None
    # Фактические координаты экземпляра (WGS-84): борта одного парка могут
    # стоять на разных площадках. Необязательны — если их нет, план берёт
    # ближайшую ВПП обстановки.
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    status: str
    valid: bool
    error: Optional[str] = None


class FleetSummary(BaseModel):
    id: str
    name: str
    # Локация парка в целом — не вводится руками, а выводится из координат
    # экземпляров при загрузке (см. fleet_service._derive_fleet_location),
    # поэтому обязательна.
    location_lat: float
    location_lon: float
    location_name: Optional[str] = None  # человекочитаемая подпись, напр. «Екатеринбург»
    uploaded_at: datetime
    status: Status
    total: int
    ready_count: int
    errors: list[FleetIssue] = []


class FleetDetail(FleetSummary):
    instances: list[FleetInstance]


class ModelSpecOut(BaseModel):
    key: str
    name: str
    uav_type: str
    speed_min_ms: float
    speed_max_ms: float
    max_flight_time_min: float
    max_route_km: Optional[float]
    max_wind_ms: float
    comm_range_km: float
    height_min_m: float
    height_max_m: Optional[float]
    compatible_cameras: list[str]
