"""Pydantic-схемы модуля «Парк БВС» — см. docs/trebovania/Парк_БВС.md.

По запросу пользователя «Парк» стал множественной, именованной сущностью
(было — единственный глобальный парк, загрузка заменяла предыдущий): можно
загрузить несколько парков с разных площадок/баз, каждый хранится отдельно и
виден карточкой на Экране «Парк». У каждого парка — собственная локация
(``location_lat``/``location_lon``, обязательна) — используется модулем
«Задача» для проверки совместимости парка и обстановки по расстоянию
(``task_service._check_fleet_environment_compatibility``), чтобы не дать
выбрать, например, парк из Екатеринбурга с обстановкой из Москвы.
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
    # Текущее местоположение экземпляра (WGS-84) — разные БВС одного парка могут
    # базироваться на разных площадках, не только на той, что выбрана в задаче.
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    status: str
    valid: bool
    error: Optional[str] = None


class FleetSummary(BaseModel):
    id: str
    name: str
    # Локация парка (WGS-84) — где базируется парк в целом, задается явно при
    # создании (не выводится из отдельных экземпляров — у них своя, часто не
    # заполненная локация, см. FleetInstance.location_lat/lon). Обязательна —
    # без нее нельзя проверить совместимость с обстановкой при постановке задачи.
    location_lat: float
    location_lon: float
    location_name: Optional[str] = None  # человекочитаемая подпись, например "Екатеринбург"
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
