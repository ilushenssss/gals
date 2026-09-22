"""Преобразование ORM-строк в pydantic-схемы API и обратно.

Отдельный модуль, потому что это единственное место, где может незаметно
поехать контракт: схемы ответов менять нельзя, а модель хранения меняется
свободно. Все поля перечислены явно — при добавлении поля в схему mapper
сломается на этапе валидации pydantic, а не отдаст молча неполный ответ.

Единственное наблюдаемое отличие после круга через БД: координаты в сыром
GeoJSON возвращаются списками, а не кортежами (так их отдает JSONB), тогда как
shapely ``mapping()`` строит кортежи. На сериализацию ответа это не влияет
(JSON одинаков), и ``shape()`` принимает оба вида, поэтому расчет не
затрагивается.
"""

from __future__ import annotations

import uuid
from typing import Any

from shapely.geometry import shape

from uav_planner.api.schemas.environment import (
    EnvironmentDetail,
    LayerCounts,
    ValidationIssue,
)
from uav_planner.api.schemas.fleet import FleetDetail, FleetInstance, FleetIssue
from uav_planner.api.schemas.job import JobInfo
from uav_planner.api.schemas.plan import PlanDetail, PlanSortie, PlanSortiePhase
from uav_planner.api.schemas.safety import SafetyCheckOut, SafetyReport, ViolationOut
from uav_planner.api.schemas.task import TaskDetail
from uav_planner.db.geo import from_db_geojson, to_db, to_db_multiline
from uav_planner.models.environment import (
    Environment,
    EnvironmentFeature,
    EnvironmentIssue,
)
from uav_planner.models.fleet import FleetInstance as FleetInstanceRow
from uav_planner.models.fleet import FleetIssue as FleetIssueRow
from uav_planner.models.fleet import FleetUpload
from uav_planner.models.job import PlanJob as PlanJobRow
from uav_planner.models.plan import Plan, PlanSortie as PlanSortieRow
from uav_planner.models.safety import SafetyCheck as SafetyCheckRow
from uav_planner.models.safety import SafetyReport as SafetyReportRow
from uav_planner.models.task import Task

LAYER_TYPES = ("launch_site", "airspace", "no_fly", "obstacle", "reserve_site")


def as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _geom_or_none(geojson: dict[str, Any] | None):
    """Геометрия из GeoJSON; None, если ее нет или она непригодна для хранения.

    Невалидные объекты обстановки (самопересечение, отсутствующая геометрия)
    сохраняются вместе с ошибкой — API обязан их вернуть, поэтому отсутствие
    geometry-колонки для них нормально.
    """
    if not geojson:
        return None
    try:
        geom = shape(geojson)
    except Exception:
        return None
    if geom.is_empty:
        return None
    try:
        return to_db(geom)
    except Exception:
        return None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# --- обстановка --------------------------------------------------------------


def environment_to_rows(detail: EnvironmentDetail) -> Environment:
    row = Environment(
        id=as_uuid(detail.id),
        name=detail.name,
        uploaded_at=detail.uploaded_at,
        status=detail.status,
        counts=detail.counts.model_dump(),
    )
    for layer in LAYER_TYPES:
        for index, feature in enumerate(detail.layers.get(layer, [])):
            props = feature.get("properties") or {}
            row.features.append(
                EnvironmentFeature(
                    layer=layer,
                    feature_index=index,
                    feature=feature,
                    geom=_geom_or_none(feature.get("geometry")),
                    buffer_geom=_geom_or_none(props.get("_buffer_geojson")),
                    is_valid=bool(props.get("_valid", True)),
                    h_min_m=_num(props.get("h_min")),
                    h_max_m=_num(props.get("h_max")),
                    safety_buffer_m=_num(props.get("safety_buffer_m")) or 0.0,
                )
            )
    for ordinal, issue in enumerate(detail.errors):
        row.issues.append(
            EnvironmentIssue(
                ordinal=ordinal,
                layer=issue.layer,
                feature_index=issue.feature_index,
                name=issue.name,
                message=issue.message,
            )
        )
    return row


def environment_from_row(row: Environment) -> EnvironmentDetail:
    layers: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYER_TYPES}
    for feature in sorted(row.features, key=lambda f: (f.layer, f.feature_index)):
        layers[feature.layer].append(feature.feature)
    return EnvironmentDetail(
        id=str(row.id),
        name=row.name,
        uploaded_at=row.uploaded_at,
        status=row.status,
        counts=LayerCounts(**row.counts),
        errors=[
            ValidationIssue(
                layer=i.layer, feature_index=i.feature_index, message=i.message, name=i.name
            )
            for i in sorted(row.issues, key=lambda i: i.ordinal)
        ],
        layers=layers,
    )


# --- парк БВС ----------------------------------------------------------------


def fleet_to_rows(detail: FleetDetail) -> FleetUpload:
    row = FleetUpload(
        id=as_uuid(detail.id),
        name=detail.name,
        location_lat=detail.location_lat,
        location_lon=detail.location_lon,
        location_name=detail.location_name,
        uploaded_at=detail.uploaded_at,
        status=detail.status,
        total=detail.total,
        ready_count=detail.ready_count,
    )
    for ordinal, inst in enumerate(detail.instances):
        row.instances.append(
            FleetInstanceRow(
                ordinal=ordinal,
                inventory_number=inst.inventory_number,
                model_key=inst.model_key,
                model_name=inst.model_name,
                base_launch_site=inst.base_launch_site,
                location_lat=inst.location_lat,
                location_lon=inst.location_lon,
                status=inst.status,
                is_valid=inst.valid,
                error=inst.error,
            )
        )
    for ordinal, issue in enumerate(detail.errors):
        row.issues.append(
            FleetIssueRow(
                ordinal=ordinal,
                inventory_number=issue.inventory_number,
                message=issue.message,
            )
        )
    return row


def fleet_from_row(row: FleetUpload) -> FleetDetail:
    return FleetDetail(
        id=str(row.id),
        name=row.name,
        location_lat=row.location_lat,
        location_lon=row.location_lon,
        location_name=row.location_name,
        uploaded_at=row.uploaded_at,
        status=row.status,
        total=row.total,
        ready_count=row.ready_count,
        errors=[
            FleetIssue(inventory_number=i.inventory_number, message=i.message)
            for i in sorted(row.issues, key=lambda i: i.ordinal)
        ],
        instances=[
            FleetInstance(
                inventory_number=i.inventory_number,
                model_key=i.model_key,
                model_name=i.model_name,
                base_launch_site=i.base_launch_site,
                location_lat=i.location_lat,
                location_lon=i.location_lon,
                status=i.status,
                valid=i.is_valid,
                error=i.error,
            )
            for i in sorted(row.instances, key=lambda i: i.ordinal)
        ],
    )


# --- задача -------------------------------------------------------------------


TASK_COLUMNS = (
    "name", "survey_type", "gsd_cm", "work_date", "window_start", "window_end",
    "wind_speed_ms", "cloud_cover_pct", "criterion_mode", "criterion_alpha",
    "status", "version", "daylight_warning", "created_at", "updated_at",
)


def apply_task(row: Task, detail: TaskDetail) -> Task:
    """Записывает поля схемы в строку — и для вставки, и для обновления."""
    row.id = as_uuid(detail.id)
    row.environment_id = as_uuid(detail.environment_id)
    row.fleet_id = as_uuid(detail.fleet_id)
    for column in TASK_COLUMNS:
        setattr(row, column, getattr(detail, column))
    row.area = detail.area
    row.area_geom = _geom_or_none(detail.area)
    row.updated_by = detail.updated_by
    return row


def task_from_row(row: Task) -> TaskDetail:
    return TaskDetail(
        id=str(row.id),
        name=row.name,
        environment_id=str(row.environment_id),
        environment_name=row.environment.name,
        fleet_id=str(row.fleet_id),
        fleet_name=row.fleet.name,
        survey_type=row.survey_type,
        work_date=row.work_date,
        status=row.status,
        version=row.version,
        daylight_warning=row.daylight_warning,
        created_at=row.created_at,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        gsd_cm=row.gsd_cm,
        window_start=row.window_start,
        window_end=row.window_end,
        wind_speed_ms=row.wind_speed_ms,
        cloud_cover_pct=row.cloud_cover_pct,
        criterion_mode=row.criterion_mode,
        criterion_alpha=row.criterion_alpha,
        area=row.area,
    )


# --- план ---------------------------------------------------------------------


def plan_to_rows(detail: PlanDetail) -> Plan:
    row = Plan(
        id=as_uuid(detail.id),
        task_id=as_uuid(detail.task_id),
        version=detail.version,
        created_at=detail.created_at,
        criterion_mode=detail.criterion_mode,
        criterion_alpha=detail.criterion_alpha,
        j1_s=detail.j1_s,
        j2_s=detail.j2_s,
        is_optimal=detail.is_optimal,
        uav_model=detail.uav_model,
        sortie_count=detail.sortie_count,
        warnings=list(detail.warnings),
        status=detail.status,
        confirmed_at=detail.confirmed_at,
        confirmed_by=detail.confirmed_by,
        exported_at=detail.exported_at,
        confirmed_with_overrides=detail.confirmed_with_overrides,
        model_key=detail.model_key,
        camera_key=detail.camera_key,
        height_m=detail.height_m,
        swath_m=detail.swath_m,
        cruise_speed_mps=detail.cruise_speed_mps,
        budget_s=detail.budget_s,
    )
    for sortie in detail.sorties:
        row.sorties.append(
            PlanSortieRow(
                uav_id=sortie.uav_id,
                sortie_index=sortie.sortie_index,
                takeoff_site=sortie.takeoff_site,
                landing_site=sortie.landing_site,
                start_utc=sortie.start_utc,
                end_utc=sortie.end_utc,
                flight_time_s=sortie.flight_time_s,
                distance_m=sortie.distance_m,
                route_geom=to_db(shape(sortie.track_geojson)),
                survey_tracks_geom=to_db_multiline(shape(sortie.survey_tracks_geojson)),
                phases=[phase.model_dump(mode="json") for phase in sortie.phases],
            )
        )
    return row


def plan_from_row(row: Plan) -> PlanDetail:
    return PlanDetail(
        id=str(row.id),
        task_id=str(row.task_id),
        version=row.version,
        created_at=row.created_at,
        criterion_mode=row.criterion_mode,
        criterion_alpha=row.criterion_alpha,
        j1_s=row.j1_s,
        j2_s=row.j2_s,
        is_optimal=row.is_optimal,
        uav_model=row.uav_model,
        sortie_count=row.sortie_count,
        warnings=list(row.warnings),
        status=row.status,
        confirmed_at=row.confirmed_at,
        confirmed_by=row.confirmed_by,
        exported_at=row.exported_at,
        confirmed_with_overrides=row.confirmed_with_overrides,
        model_key=row.model_key,
        camera_key=row.camera_key,
        height_m=row.height_m,
        swath_m=row.swath_m,
        cruise_speed_mps=row.cruise_speed_mps,
        budget_s=row.budget_s,
        sorties=[
            PlanSortie(
                uav_id=s.uav_id,
                sortie_index=s.sortie_index,
                takeoff_site=s.takeoff_site,
                landing_site=s.landing_site,
                start_utc=s.start_utc,
                end_utc=s.end_utc,
                flight_time_s=s.flight_time_s,
                distance_m=s.distance_m,
                track_geojson=from_db_geojson(s.route_geom),
                survey_tracks_geojson=from_db_geojson(s.survey_tracks_geom),
                phases=[PlanSortiePhase(**phase) for phase in (s.phases or [])],
            )
            for s in sorted(row.sorties, key=lambda s: (s.uav_id, s.sortie_index))
        ],
    )


# --- проверка безопасности -----------------------------------------------------


def safety_report_to_rows(report: SafetyReport, requested_plan_id: str) -> SafetyReportRow:
    row = SafetyReportRow(
        id=as_uuid(report.id),
        plan_id=as_uuid(report.plan_id),
        requested_plan_id=as_uuid(requested_plan_id),
        task_id=as_uuid(report.task_id),
        created_at=report.created_at,
        status=report.status,
        auto_recalc_count=report.auto_recalc_count,
    )
    for ordinal, check in enumerate(report.checks):
        row.checks.append(
            SafetyCheckRow(
                ordinal=ordinal,
                name=check.name,
                label=check.label,
                passed=check.passed,
                violations=[v.model_dump(mode="json") for v in check.violations],
            )
        )
    return row


def safety_report_from_row(row: SafetyReportRow) -> SafetyReport:
    checks = [
        SafetyCheckOut(
            name=c.name,
            label=c.label,
            passed=c.passed,
            violations=[ViolationOut(**v) for v in (c.violations or [])],
        )
        for c in sorted(row.checks, key=lambda c: c.ordinal)
    ]
    # Признак «все нарушения приняты» выводится из самих нарушений, а не
    # хранится колонкой: иначе флаг и список могли бы разойтись.
    all_violations = [v for c in checks for v in c.violations]
    return SafetyReport(
        id=str(row.id),
        plan_id=str(row.plan_id),
        task_id=str(row.task_id),
        created_at=row.created_at,
        status=row.status,
        auto_recalc_count=row.auto_recalc_count,
        checks=checks,
        violations_acknowledged=bool(all_violations) and all(v.ignored for v in all_violations),
    )


def job_from_row(row: PlanJobRow) -> JobInfo:
    return JobInfo(
        id=str(row.id),
        task_id=str(row.task_id),
        kind=row.kind,
        status=row.status,
        stage=row.stage,
        progress=row.progress,
        error=row.error,
        error_code=row.error_code,
        cancel_requested=row.cancel_requested,
        plan_id=str(row.plan_id) if row.plan_id else None,
        result_plan_id=str(row.result_plan_id) if row.result_plan_id else None,
        result_report_id=str(row.result_report_id) if row.result_report_id else None,
        auto_recalc_count=row.auto_recalc_count,
        queued_at=row.queued_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )
