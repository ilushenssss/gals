"""Pydantic-схемы модуля «Планирование» — см. docs/trebovania/Планирование.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class PlanSortiePhase(BaseModel):
    """Один этап вылета (взлет и перелет до зоны, конкретный галс, переход
    между галсами, возврат и посадка) с собственным интервалом времени —
    раскрывается по клику на пункт расписания на Экране 4 (ИНТ.ФТ.15)."""

    label: str
    kind: str  # "transit" | "survey"
    start_utc: datetime
    end_utc: datetime
    distance_m: float


class PlanSortie(BaseModel):
    uav_id: str
    sortie_index: int
    takeoff_site: Optional[str] = None
    landing_site: Optional[str] = None
    start_utc: datetime
    end_utc: datetime
    flight_time_s: float
    distance_m: float
    track_geojson: dict[str, Any]  # LineString, WGS-84 — маршрут вылета целиком (галсы + переходы), для карты
    survey_tracks_geojson: dict[str, Any]  # MultiLineString, WGS-84 — только галсы, без переходов (для проверки покрытия)
    phases: list[PlanSortiePhase] = []


class PlanSummary(BaseModel):
    id: str
    task_id: str
    version: int
    created_at: datetime
    criterion_mode: str
    criterion_alpha: float
    j1_s: float
    j2_s: float
    is_optimal: bool
    uav_model: str
    sortie_count: int
    warnings: list[str] = []


class PlanDetail(PlanSummary):
    # Заявленные параметры расчета — независимая проверка (модуль «Проверка
    # безопасности») сверяет маршруты и расписание именно с ними, не
    # обращаясь к внутренним данным решателя.
    model_key: str
    camera_key: str
    height_m: float
    swath_m: float
    cruise_speed_mps: float
    budget_s: float
    sorties: list[PlanSortie]
