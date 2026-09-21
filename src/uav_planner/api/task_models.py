"""Pydantic-схемы модуля «Задача» — см. docs/trebovania/Задача.md.

Область облета передается файлом (GeoJSON Polygon/MultiPolygon, WGS-84) —
объекты на карте не редактируются (см. ответы экспертов, п. 11), полигон
только загружается или выбирается из ранее загруженных задач.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel

SurveyType = Literal["RGB", "мультиспектральная", "ИК", "LiDAR", "геофизическая"]
CriterionMode = Literal["Время", "Налет", "Компромисс"]
TaskStatus = Literal["Черновик", "Рассчитана", "Подтверждена"]

SURVEY_TYPES: tuple[SurveyType, ...] = ("RGB", "мультиспектральная", "ИК", "LiDAR", "геофизическая")


class TaskValidationIssue(BaseModel):
    field: str
    message: str


class TaskSummary(BaseModel):
    id: str
    name: str
    environment_id: str
    environment_name: str
    # Парк БВС, выбранный для этой задачи (ЗАД: теперь парков несколько,
    # выбор — часть постановки задачи; совместимость с обстановкой по
    # расстоянию проверена при создании, см. task_service._validate).
    fleet_id: str
    fleet_name: str
    survey_type: SurveyType
    work_date: date
    status: TaskStatus
    version: int
    daylight_warning: str | None = None
    created_at: datetime
    updated_at: datetime
    # GeoJSON geometry, WGS-84 — в сводке (не только в детали), чтобы список задач
    # мог отрисовать миниатюру области на карточке без отдельного запроса на
    # каждую задачу (ИНТ: галерея карточек списка задач).
    area: dict[str, Any]


class TaskDetail(TaskSummary):
    gsd_cm: float
    window_start: time | None
    window_end: time | None
    wind_speed_ms: float | None
    cloud_cover_pct: float | None
    criterion_mode: CriterionMode
    criterion_alpha: float
