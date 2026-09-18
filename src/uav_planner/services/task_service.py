"""Логика модуля «Задача»: ЗАД.ФТ.2-5 (проверки), ЗАД.ФТ.9-10 (создание),
ЗАД.ФТ.12 (конфликты при редактировании). См. docs/trebovania/Задача.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.domain.errors import ConflictError, ValidationError
from uav_planner.geometry import GeometryError, Projector, validate_polygon
from uav_planner.schedule import daylight_window_utc_hours

from . import environment_service
from uav_planner.api.schemas.task import SURVEY_TYPES, TaskDetail, TaskSummary, TaskValidationIssue


_SUMMARY_ONLY_EXCLUDE = {
    "gsd_cm", "window_start", "window_end", "wind_speed_ms",
    "cloud_cover_pct", "criterion_mode", "criterion_alpha", "area",
}


class TaskValidationError(ValidationError):
    def __init__(self, issues: list[TaskValidationIssue]):
        super().__init__("; ".join(f"{i.field}: {i.message}" for i in issues))
        self.issues = issues


class TaskConflictError(ConflictError):
    """ЗАД.ФТ.12: версия задачи в хранилище не совпадает с ожидаемой."""


class TaskNotEditableError(ConflictError, ValueError):
    """Редактировать нельзя задачу в статусе «Подтверждена» (план уже подтвержден
    и неизменен, см. ЭКС.ФТ.3) — только «Черновик» и «Рассчитана»."""


def _to_summary(detail: TaskDetail) -> TaskSummary:
    return TaskSummary(**detail.model_dump(exclude=_SUMMARY_ONLY_EXCLUDE))


def _hours_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _daylight_warning(area_geom: BaseGeometry, work_date: date, window_start: time | None, window_end: time | None) -> str | None:
    lat, lon = area_geom.centroid.y, area_geom.centroid.x
    window = daylight_window_utc_hours(lat, lon, work_date)
    if window is None:
        return "Полярная ночь на эту дату в этих координатах — световой день отсутствует."

    op_start = window_start.hour + window_start.minute / 60.0 if window_start else 0.0
    op_end = window_end.hour + window_end.minute / 60.0 if window_end else 24.0
    if not _hours_overlap((op_start, op_end), window):
        return (
            "Окно работ не пересекается со световым днем для этой даты и координат "
            "(восход/закат UTC: %.1f/%.1f ч) — вылеты будут невозможны." % window
        )
    return None


def _validate(
    *,
    environment_id: str,
    survey_type: str,
    gsd_cm: float,
    work_date: date,
    window_start: time | None,
    window_end: time | None,
    criterion_mode: str,
    criterion_alpha: float | None,
    area_geojson: dict[str, Any],
) -> tuple[Any, float | None, BaseGeometry | None]:
    """Проверки ЗАД.ФТ.2-5. Возвращает (обстановка, alpha, area_geom) или бросает TaskValidationError."""
    issues: list[TaskValidationIssue] = []

    try:
        env = environment_service.get_environment(environment_id)
    except KeyError:
        raise TaskValidationError([TaskValidationIssue(field="environment_id", message="обстановка не найдена")])
    if env.status != "Корректна":
        raise TaskValidationError([
            TaskValidationIssue(field="environment_id", message="обстановка содержит ошибки и недоступна для постановки задачи")
        ])

    if survey_type not in SURVEY_TYPES:
        issues.append(TaskValidationIssue(field="survey_type", message=f"недопустимый тип съемки: {survey_type!r}"))
    if gsd_cm <= 0:
        issues.append(TaskValidationIssue(field="gsd_cm", message="целевое разрешение GSD должно быть положительным"))

    alpha: float | None
    if criterion_mode == "Компромисс":
        if criterion_alpha is None or not (0.0 <= criterion_alpha <= 1.0):
            issues.append(TaskValidationIssue(field="criterion_alpha", message="для критерия «Компромисс» нужен вес α в диапазоне [0, 1]"))
        alpha = criterion_alpha
    elif criterion_mode == "Время":
        alpha = 1.0
    elif criterion_mode == "Налет":
        alpha = 0.0
    else:
        issues.append(TaskValidationIssue(field="criterion_mode", message=f"недопустимый критерий: {criterion_mode!r}"))
        alpha = None

    if window_start is not None and window_end is not None and window_start > window_end:
        issues.append(TaskValidationIssue(field="window", message="начало окна работ позже его окончания"))

    area_geom: BaseGeometry | None = None
    try:
        candidate = shape(area_geojson)
        validate_polygon(candidate)
        area_geom = candidate
    except (GeometryError, ValueError, TypeError, KeyError) as exc:
        issues.append(TaskValidationIssue(field="area", message=str(exc)))

    if area_geom is not None:
        airspace_geoms = [
            shape(f["geometry"])
            for f in env.layers.get("airspace", [])
            if f.get("properties", {}).get("_valid", True)
        ]
        if not airspace_geoms:
            issues.append(TaskValidationIssue(field="area", message="в выбранной обстановке нет ни одной зоны разрешенного пространства"))
        else:
            projector = Projector.for_geometry(unary_union([area_geom, *airspace_geoms]))
            area_utm = projector.to_utm(area_geom)
            allowed_union = unary_union([projector.to_utm(g) for g in airspace_geoms])
            if not area_utm.intersects(allowed_union):
                issues.append(TaskValidationIssue(
                    field="area",
                    message="область облета не пересекается ни с одной зоной разрешенного воздушного пространства выбранной обстановки",
                ))

    if issues:
        raise TaskValidationError(issues)

    return env, alpha, area_geom


def create_task(
    *,
    name: str,
    environment_id: str,
    survey_type: str,
    gsd_cm: float,
    work_date: date,
    window_start: time | None,
    window_end: time | None,
    wind_speed_ms: float | None,
    cloud_cover_pct: float | None,
    criterion_mode: str,
    criterion_alpha: float | None,
    area_geojson: dict[str, Any],
) -> TaskSummary:
    env, alpha, area_geom = _validate(
        environment_id=environment_id, survey_type=survey_type, gsd_cm=gsd_cm, work_date=work_date,
        window_start=window_start, window_end=window_end, criterion_mode=criterion_mode,
        criterion_alpha=criterion_alpha, area_geojson=area_geojson,
    )
    warning = _daylight_warning(area_geom, work_date, window_start, window_end)

    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    detail = TaskDetail(
        id=task_id, name=name, environment_id=environment_id, environment_name=env.name,
        survey_type=survey_type, work_date=work_date, status="Черновик", version=1,
        daylight_warning=warning, created_at=now, updated_at=now, gsd_cm=gsd_cm,
        window_start=window_start, window_end=window_end, wind_speed_ms=wind_speed_ms,
        cloud_cover_pct=cloud_cover_pct, criterion_mode=criterion_mode, criterion_alpha=alpha,
        area=area_geojson,
    )
    repositories.tasks.put(task_id, detail)
    return _to_summary(detail)


def update_task(
    task_id: str,
    expected_version: int,
    *,
    name: str,
    survey_type: str,
    gsd_cm: float,
    work_date: date,
    window_start: time | None,
    window_end: time | None,
    wind_speed_ms: float | None,
    cloud_cover_pct: float | None,
    criterion_mode: str,
    criterion_alpha: float | None,
    area_geojson: dict[str, Any],
) -> TaskSummary:
    """ЗАД.ФТ.9-10 (редактирование) + ЗАД.ФТ.12 (оптимистичная блокировка версии)."""
    existing = repositories.tasks.get(task_id)  # KeyError -> 404 в routes

    if existing.status == "Подтверждена":
        raise TaskNotEditableError("план по этой задаче подтвержден — редактирование недоступно")
    if existing.version != expected_version:
        raise TaskConflictError(f"задача изменена другим пользователем (текущая версия {existing.version})")

    env, alpha, area_geom = _validate(
        environment_id=existing.environment_id, survey_type=survey_type, gsd_cm=gsd_cm, work_date=work_date,
        window_start=window_start, window_end=window_end, criterion_mode=criterion_mode,
        criterion_alpha=criterion_alpha, area_geojson=area_geojson,
    )
    warning = _daylight_warning(area_geom, work_date, window_start, window_end)

    updated = existing.model_copy(update={
        "name": name, "survey_type": survey_type, "work_date": work_date,
        "version": existing.version + 1, "daylight_warning": warning,
        # Правка параметров делает прежний план (если он был) неактуальным —
        # задача возвращается в "Черновик" до нового расчета (ПЛН.ФТ.8).
        "status": "Черновик",
        "updated_at": datetime.now(timezone.utc), "gsd_cm": gsd_cm,
        "window_start": window_start, "window_end": window_end, "wind_speed_ms": wind_speed_ms,
        "cloud_cover_pct": cloud_cover_pct, "criterion_mode": criterion_mode,
        "criterion_alpha": alpha, "area": area_geojson,
    })
    repositories.tasks.put(task_id, updated)
    return _to_summary(updated)


def mark_calculated(task_id: str) -> None:
    """Вызывается модулем «Планирование» после успешного расчета плана (ПЛН.ФТ.5)."""
    existing = repositories.tasks.get(task_id)
    repositories.tasks.put(
        task_id,
        existing.model_copy(update={"status": "Рассчитана", "updated_at": datetime.now(timezone.utc)}),
    )


def list_tasks(environment_id: str | None = None) -> list[TaskSummary]:
    return [
        _to_summary(t)
        for t in repositories.tasks.list_by_environment(environment_id)
    ]


def get_task(task_id: str) -> TaskDetail:
    return repositories.tasks.get(task_id)
