"""Pydantic-схемы модуля «Планирование» — см. docs/trebovania/Планирование.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel

# ЭКС.ФТ.5: жизненный цикл плана. «Проверен» здесь означает исключительно
# «последняя проверка безопасности пройдена без нарушений» — версия плана,
# провалившая проверку, откатывается обратно в «Черновик» (см.
# plan_service.mark_reviewed): статус «Проверен с нарушениями» из требования
# отображается отдельно, в самом модуле «Проверка безопасности»
# (SafetyReport.status), и намеренно не хранится как отдельное значение
# здесь — им бы никто не пользовался в этом модуле, кроме как для запрета
# подтверждения, а этот запрет и так следует из того, что статус не «Проверен».
PlanStatus = Literal["Черновик", "Проверен", "Подтвержден", "Выгружен"]


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
    status: PlanStatus = "Черновик"
    # ЭКС.ФТ.6/9: фиксация подтверждения. confirmed_by — свободный текст «ФИО»,
    # введенный оператором в форме подтверждения (в системе нет модели
    # пользователей/аутентификации — см. main/README.md, раздел «Подтверждение
    # и экспорт» — это честно задокументированное упрощение v1, а не
    # полноценная идентификация).
    confirmed_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    exported_at: Optional[datetime] = None  # ЭКС.ФТ.7: время первого успешного экспорта
    # true, если план подтвержден в обход обычного запрета на нарушения (ЭКС.ФТ.2) —
    # оператор явно отметил каждое нарушение последнего отчета как принятое, см.
    # safety_service.set_violation_ignored/SafetyReport.violations_acknowledged.
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
