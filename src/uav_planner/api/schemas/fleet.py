"""Pydantic-схемы модуля «Парк БВС» — см. docs/trebovania/Парк_БВС.md.

Парк — один текущий загруженный список экземпляров БВС (не история версий,
как обстановки): повторная загрузка полностью заменяет предыдущий список.
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
    status: str
    valid: bool
    error: Optional[str] = None


class FleetSummary(BaseModel):
    id: str
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
