"""Pydantic-схемы модуля «Планирование» — см. docs/trebovania/Планирование.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel

# Жизненный цикл плана, ЭКС.ФТ.5.
PlanStatus = Literal["Черновик", "Проверен", "Подтвержден", "Выгружен"]


class PlanSortiePhase(BaseModel):
    """Один этап вылета: перелёт до зоны задания, галс, переход между галсами,
    возврат. Нужен, чтобы раскрыть пункт расписания на карте (ИНТ.ФТ.15) —
    переходы теперь строятся в обход зон и заметно отличаются от прямой."""

    label: str
    kind: str  # "transit" | "survey"
    start_utc: datetime
    end_utc: datetime
    distance_m: float
    # Средняя высота этапа над рельефом (AGL), если облет рельефа включен и
    # рельеф удалось получить — иначе None (высота держится абсолютной,
    # см. PlanDetail.warnings). Только AGL, не абсолютная Z: абсолютная
    # высота — в 3D-координатах track_geojson/survey_tracks_geojson.
    height_agl_m: Optional[float] = None


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
    # Параметры съемки именно этого вылета. В смешанном парке у вылетов
    # разных моделей они разные, и проверка безопасности и выгрузка берут их
    # отсюда. None — план рассчитан до появления полей: действуют параметры
    # карточки плана (PlanDetail).
    model_key: Optional[str] = None
    camera_key: Optional[str] = None
    height_m: Optional[float] = None
    swath_m: Optional[float] = None
    cruise_speed_mps: Optional[float] = None
    budget_s: Optional[float] = None


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
    # Жизненный цикл ЭКС.ФТ.5: Черновик -> Проверен -> Подтвержден -> Выгружен.
    # Статус карточки плана, не результат проверки: план со статусом «Проверен»
    # может содержать нарушения — тогда подтверждение запрещено (ЭКС.ФТ.2).
    status: PlanStatus = "Черновик"
    confirmed_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None  # ФИО подтвердившего, нужен сообщению ЭКС.ФТ.9
    exported_at: Optional[datetime] = None
    # Подтверждён вопреки нарушениям: оператор пометил принятыми все нарушения
    # последнего отчёта (расширение ЭКС.ФТ.2 по запросу пользователя).
    confirmed_with_overrides: bool = False


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
    # Простым языком, с формулами и подставленными числами — что и как
    # считалось, начиная с расчёта галсов, почему выбран именно этот
    # кандидат «модель+камера» (см. plan_service.build_candidate/
    # _pick_best_candidate). Только на полном виде плана, как sorties —
    # в PlanSummary не нужен.
    calculation_log: list[str] = []
