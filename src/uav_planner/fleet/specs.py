"""Паспортный справочник трех моделей БВС Геоскан — модуль «Парк БВС».

Источник данных — концепция решения, раздел 3 («Парк БВС»). Высотный диапазон
и совместимая нагрузка берутся из ``uav_planner.camera.specs.UAV_MODELS`` —
единственного источника истины для этих двух полей (используются также
модулем ``camera` при расчете высоты съемки и допустимости связки БВС+камера);
здесь они не переопределяются, а переиспользуются, чтобы справочники не могли
разойтись.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from uav_planner.camera.specs import UAV_MODELS as _CAMERA_MODELS

READINESS_STATUSES: tuple[str, ...] = ("Готов", "Недоступен", "На обслуживании")


@dataclass(frozen=True)
class SpeedRange:
    min_ms: float
    max_ms: float


@dataclass(frozen=True)
class UavModelSpec:
    """Паспортные характеристики модели БВС для отображения в справочнике
    (ПБС.ФТ.10) и последующей фильтрации кандидатов модулем «Планирование»."""

    key: str
    name: str
    uav_type: str  # "самолет" | "квадрокоптер"
    speed_ms: SpeedRange
    max_flight_time_min: float
    max_route_km: Optional[float]
    max_wind_ms: float
    comm_range_km: float
    height_min_m: float
    height_max_m: Optional[float]
    compatible_cameras: tuple[str, ...]


def _spec(key: str, **kwargs) -> UavModelSpec:
    camera_model = _CAMERA_MODELS[key]
    return UavModelSpec(
        key=key,
        name=camera_model.name,
        height_min_m=camera_model.height_limits.h_min,
        height_max_m=camera_model.height_limits.h_max,
        compatible_cameras=camera_model.compatible_cameras,
        **kwargs,
    )


FLEET_MODELS: dict[str, UavModelSpec] = {
    "geoscan-201": _spec(
        "geoscan-201",
        uav_type="самолет",
        speed_ms=SpeedRange(17.8, 36.1),
        max_flight_time_min=180.0,
        max_route_km=210.0,
        max_wind_ms=12.0,
        comm_range_km=40.0,
    ),
    "geoscan-gemini": _spec(
        "geoscan-gemini",
        uav_type="квадрокоптер",
        speed_ms=SpeedRange(0.0, 15.0),
        max_flight_time_min=40.0,
        max_route_km=None,
        max_wind_ms=10.0,
        comm_range_km=5.0,
    ),
    "geoscan-801": _spec(
        "geoscan-801",
        uav_type="квадрокоптер",
        speed_ms=SpeedRange(0.0, 15.0),
        max_flight_time_min=40.0,
        max_route_km=30.0,
        max_wind_ms=12.0,
        comm_range_km=10.0,
    ),
}
