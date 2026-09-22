"""Логика модуля «Задача»: ЗАД.ФТ.2-5 (проверки), ЗАД.ФТ.9-10 (создание),
ЗАД.ФТ.12 (конфликты при редактировании). См. docs/trebovania/Задача.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from shapely.errors import ShapelyError
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.api.deps import DEFAULT_USER
from uav_planner.domain.errors import ConflictError, ValidationError
from uav_planner.geometry import GeometryError, Projector, geodesic_distance_m, validate_polygon
from uav_planner.schedule import daylight_window_utc_hours

from . import environment_service
from . import fleet_service
from uav_planner.api.schemas.task import SURVEY_TYPES, TaskDetail, TaskSummary, TaskValidationIssue


_SUMMARY_ONLY_EXCLUDE = {
    "gsd_cm", "window_start", "window_end", "wind_speed_ms",
    "cloud_cover_pct", "criterion_mode", "criterion_alpha",
}

# Порог совместимости парка и обстановки по расстоянию (по запросу
# пользователя: нельзя взять парк из Екатеринбурга к обстановке из Москвы).
# 150 км — заведомо больше типичного вылета БВС, но не мешает парку и
# обстановке в пределах одной области. Настраиваемая v1-константа, не из ТЗ.
MAX_FLEET_ENVIRONMENT_DISTANCE_KM = 150.0


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


AREA_FORMAT_HINT = (
    "область облета должна быть одним полигоном: допустимы GeoJSON-геометрия "
    "Polygon/MultiPolygon, Feature с такой геометрией или FeatureCollection "
    "ровно с одним таким Feature"
)


def unwrap_area_geojson(data: Any) -> dict[str, Any]:
    """Снять обертку Feature/FeatureCollection с файла области облета.

    Сервисы и хранилище работают с голой геометрией: ``plan_service`` и
    ``safety_service`` зовут ``shape(task.area)``, а в таблице рядом с сырым
    jsonb лежит индексируемая ``geometry(4326)``. Поэтому обертка снимается
    один раз, на входе, а не разбирается каждым, кто читает задачу.

    ``FeatureCollection`` принимается потому, что «нарисовал область и
    сохранил» большинство редакторов (QGIS, geojson.io) отдает именно
    коллекцией — отказывать ей как непонятному типу значит отказывать самому
    обычному файлу. Коллекция из нескольких объектов все равно отвергается:
    область облета по ЗАД.ФТ.4 одна, и молча взять из файла первый попавшийся
    полигон хуже, чем сказать об этом вслух.
    """
    if not isinstance(data, dict):
        raise ValidationError(AREA_FORMAT_HINT)

    kind = data.get("type")
    if kind == "FeatureCollection":
        features = data.get("features") or []
        if len(features) != 1:
            raise ValidationError(
                f"в файле области облета {len(features)} объект(ов) — {AREA_FORMAT_HINT}"
            )
        return unwrap_area_geojson(features[0])
    if kind == "Feature":
        return unwrap_area_geojson(data.get("geometry"))
    return data


def _check_fleet_environment_compatibility(fleet, env) -> TaskValidationIssue | None:
    """Парк и обстановка должны быть географически близко, иначе БВС физически
    не долетят до области работ.

    Считается по геодезическому расстоянию (эллипсоид WGS-84): локальная
    UTM-проекция на масштабе «разные города» уже непригодна. Если у обстановки
    нет валидных зон ``airspace``, сравнивать не с чем — проверка пропускается,
    это уже другая ошибка.
    """
    env_location = environment_service.environment_location(env)
    if env_location is None:
        return None
    distance_km = geodesic_distance_m(
        fleet.location_lon, fleet.location_lat, env_location[1], env_location[0]
    ) / 1000.0
    if distance_km > MAX_FLEET_ENVIRONMENT_DISTANCE_KM:
        return TaskValidationIssue(
            field="fleet_id",
            message=(
                f"парк «{fleet.name}» слишком далеко от обстановки «{env.name}» "
                f"({distance_km:.0f} км, допустимо не более {MAX_FLEET_ENVIRONMENT_DISTANCE_KM:.0f} км) — "
                "выберите парк, базирующийся ближе к области работ"
            ),
        )
    return None


def _validate(
    *,
    environment_id: str,
    fleet_id: str,
    survey_type: str,
    gsd_cm: float,
    work_date: date,
    window_start: time | None,
    window_end: time | None,
    criterion_mode: str,
    criterion_alpha: float | None,
    area_geojson: dict[str, Any],
) -> tuple[Any, Any, float | None, BaseGeometry | None]:
    """Проверки ЗАД.ФТ.2-5. Возвращает (обстановка, парк, alpha, area_geom) или бросает TaskValidationError."""
    issues: list[TaskValidationIssue] = []

    try:
        env = environment_service.get_environment(environment_id)
    except KeyError:
        raise TaskValidationError([TaskValidationIssue(field="environment_id", message="обстановка не найдена")])
    if env.status != "Корректна":
        raise TaskValidationError([
            TaskValidationIssue(field="environment_id", message="обстановка содержит ошибки и недоступна для постановки задачи")
        ])

    try:
        fleet = fleet_service.get_fleet(fleet_id)
    except KeyError:
        raise TaskValidationError([TaskValidationIssue(field="fleet_id", message="парк не найден")])
    if fleet.status != "Корректна":
        issues.append(TaskValidationIssue(field="fleet_id", message="парк содержит ошибки и недоступен для постановки задачи"))
    else:
        compat_issue = _check_fleet_environment_compatibility(fleet, env)
        if compat_issue:
            issues.append(compat_issue)

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
    except (GeometryError, ShapelyError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # Список широкий намеренно: файл области рисует оператор, и shapely на
        # каждом виде мусора падает по-своему — GeometryTypeError на чужом
        # "type", KeyError без "coordinates", AttributeError на объекте без
        # "type" вообще. Любой из них — некорректный ввод (400 по ЗАД.ФТ.5),
        # а не сбой сервиса, и раньше GeometryTypeError улетал наружу как 500.
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

    return env, fleet, alpha, area_geom


def create_task(
    *,
    name: str,
    environment_id: str,
    fleet_id: str,
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
    user: str = DEFAULT_USER,
) -> TaskSummary:
    env, fleet, alpha, area_geom = _validate(
        environment_id=environment_id, fleet_id=fleet_id, survey_type=survey_type, gsd_cm=gsd_cm,
        work_date=work_date, window_start=window_start, window_end=window_end,
        criterion_mode=criterion_mode, criterion_alpha=criterion_alpha, area_geojson=area_geojson,
    )
    warning = _daylight_warning(area_geom, work_date, window_start, window_end)

    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    detail = TaskDetail(
        id=task_id, name=name, environment_id=environment_id, environment_name=env.name,
        fleet_id=fleet_id, fleet_name=fleet.name,
        survey_type=survey_type, work_date=work_date, status="Черновик", version=1,
        daylight_warning=warning, created_at=now, updated_at=now, gsd_cm=gsd_cm,
        window_start=window_start, window_end=window_end, wind_speed_ms=wind_speed_ms,
        cloud_cover_pct=cloud_cover_pct, criterion_mode=criterion_mode, criterion_alpha=alpha,
        area=area_geojson, updated_by=user,
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
    user: str = DEFAULT_USER,
) -> TaskSummary:
    """ЗАД.ФТ.9-10 (редактирование) + ЗАД.ФТ.12 (оптимистичная блокировка версии)."""
    existing = repositories.tasks.get(task_id)  # KeyError -> 404 в routes

    if existing.status == "Подтверждена":
        raise TaskNotEditableError("план по этой задаче подтвержден — редактирование недоступно")
    if existing.version != expected_version:
        # ЗАД.ФТ.12 требует показать, кто именно изменил задачу. Имени может и
        # не быть (задачу создали до появления заголовка) — тогда безличная
        # формулировка, но версия называется всегда.
        who = f" ({existing.updated_by})" if existing.updated_by else ""
        raise TaskConflictError(
            f"задача изменена другим пользователем{who} "
            f"— текущая версия {existing.version}"
        )

    # Парк задачи сменить нельзя — он часть постановки, как и обстановка.
    env, fleet, alpha, area_geom = _validate(
        environment_id=existing.environment_id, fleet_id=existing.fleet_id, survey_type=survey_type,
        gsd_cm=gsd_cm, work_date=work_date, window_start=window_start, window_end=window_end,
        criterion_mode=criterion_mode, criterion_alpha=criterion_alpha, area_geojson=area_geojson,
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
        "criterion_alpha": alpha, "area": area_geojson, "updated_by": user,
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


def mark_confirmed(task_id: str) -> None:
    """Вызывается модулем «Подтверждение и экспорт» после подтверждения плана
    (ЭКС.ФТ.6): блокирует редактирование параметров задачи, пока она в этом
    статусе.

    Новый расчёт по той же задаче (``mark_calculated``) снова переводит её в
    «Рассчитана» и разблокирует правку — подтверждение относится к конкретной
    версии плана, а не запрещает считать новые (ЭКС.ФТ.3).
    """
    existing = repositories.tasks.get(task_id)
    repositories.tasks.put(
        task_id,
        existing.model_copy(update={"status": "Подтверждена", "updated_at": datetime.now(timezone.utc)}),
    )


def list_tasks(environment_id: str | None = None) -> list[TaskSummary]:
    return [
        _to_summary(t)
        for t in repositories.tasks.list_by_environment(environment_id)
    ]


def get_task(task_id: str) -> TaskDetail:
    return repositories.tasks.get(task_id)
