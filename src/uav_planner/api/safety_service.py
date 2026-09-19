"""Оркестрация модуля «Проверка безопасности» — независимая от расчетного
ядра проверка готового плана. См. docs/trebovania/Проверка_безопасности.md
(БЕЗ.ФТ.1-6) и uav_planner.safety (семь чистых проверок).

Модуль сознательно не переиспользует внутренние объекты ``plan_service``
(зоны, рабочую область) — заново разбирает слои обстановки из тех же исходных
данных, что и «Планирование», но собственным кодом. Так ошибка в допущениях
или парсинге ядра не повторится незамеченной в проверке (см. «контракт между
ролями» плана реализации: проверку пишет роль «И», независимо от ядра).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner.geometry import (
    AllowedZone,
    GeometryError,
    HeightRange,
    NoFlyZone,
    Obstacle,
    Projector,
    compute_working_area,
)
from uav_planner.safety import (
    CheckResult,
    SortieTrack,
    Violation,
    check_allowed_space,
    check_coverage,
    check_daylight,
    check_energy,
    check_geozones,
    check_reachability,
    check_separation,
)

from . import plan_service
from . import service as environment_service
from . import task_service
from .plan_models import PlanDetail
from .safety_models import SafetyCheckOut, SafetyReport, ViolationOut

MAX_AUTO_RECALC = 3

_LABELS = {
    "geozones": "Геозоны",
    "airspace": "Разрешенное пространство",
    "energy": "Энергия",
    "reachability": "Достижимость площадки",
    "coverage": "Покрытие",
    "daylight": "Световой день",
    "separation": "Разведение",
}

_reports_by_plan: dict[str, list[SafetyReport]] = {}
_attempts_by_task_version: dict[tuple[str, int], int] = {}


class SafetyCheckError(ValueError):
    """Проверку невозможно выполнить (например, план ссылается на удаленную обстановку)."""


def _valid_features(env, layer: str) -> list[dict]:
    return [f for f in env.layers.get(layer, []) if f.get("properties", {}).get("_valid", True)]


def _time_to_hours(t) -> float | None:
    return None if t is None else t.hour + t.minute / 60.0 + t.second / 3600.0


def _violation_out(v: Violation, projector: Projector) -> ViolationOut:
    if v.point is None:
        return ViolationOut(message=v.message)
    wgs = projector.to_wgs84(v.point)
    return ViolationOut(message=v.message, lat=wgs.y, lon=wgs.x)


def _combine(name: str, results: list[CheckResult], projector: Projector) -> SafetyCheckOut:
    violations: list[Violation] = []
    for r in results:
        violations.extend(r.violations)
    unique: dict[str, Violation] = {}
    for v in violations:
        unique.setdefault(v.message, v)
    shown = list(unique.values())[:5]
    if len(unique) > 5:
        shown.append(Violation(f"...и еще {len(unique) - 5} нарушени(й)"))
    passed = all(r.passed for r in results) if results else True
    return SafetyCheckOut(
        name=name, label=_LABELS[name], passed=passed,
        violations=[_violation_out(v, projector) for v in shown],
    )


def _run_checks(env, task, plan: PlanDetail) -> list[SafetyCheckOut]:
    airspace_feats = _valid_features(env, "airspace")
    no_fly_feats = _valid_features(env, "no_fly")
    obstacle_feats = _valid_features(env, "obstacle")
    launch_feats = _valid_features(env, "launch_site")
    reserve_feats = _valid_features(env, "reserve_site")

    area_geom = shape(task.area)
    all_geoms: list[BaseGeometry] = [area_geom]
    all_geoms += [
        shape(f["geometry"])
        for f in airspace_feats + no_fly_feats + obstacle_feats + launch_feats + reserve_feats
    ]
    projector = Projector.for_geometry(unary_union(all_geoms))

    allowed_zones = [
        AllowedZone(
            id=str(i), polygon=projector.to_utm(shape(f["geometry"])),
            height=HeightRange(float(f["properties"]["h_min"]), float(f["properties"]["h_max"])),
        )
        for i, f in enumerate(airspace_feats)
    ]
    no_fly_zones = [
        NoFlyZone(
            id=str(i), polygon=projector.to_utm(shape(f["geometry"])),
            safety_buffer_m=float(f["properties"].get("safety_buffer_m", 0.0)),
        )
        for i, f in enumerate(no_fly_feats)
    ]
    obstacles = [
        Obstacle(
            id=str(i), polygon=projector.to_utm(shape(f["geometry"])),
            height=HeightRange(float(f["properties"]["h_min"]), float(f["properties"]["h_max"])),
        )
        for i, f in enumerate(obstacle_feats)
    ]

    allowed_union = unary_union([z.polygon for z in allowed_zones]) if allowed_zones else None
    no_fly_footprints = [z.footprint() for z in no_fly_zones]
    obstacle_footprints = [o.footprint() for o in obstacles if o.is_hole_at(plan.height_m)]
    landing_points = [projector.to_utm(shape(f["geometry"])) for f in launch_feats + reserve_feats]

    lat, lon = area_geom.centroid.y, area_geom.centroid.x
    window_start_hour = _time_to_hours(task.window_start) or 0.0
    window_end_hour = _time_to_hours(task.window_end) or 24.0

    geozone_results: list[CheckResult] = []
    airspace_results: list[CheckResult] = []
    energy_results: list[CheckResult] = []
    reachability_results: list[CheckResult] = []
    daylight_results: list[CheckResult] = []
    sortie_tracks: list[SortieTrack] = []
    all_survey_tracks_utm: list[BaseGeometry] = []

    for sortie in plan.sorties:
        route_utm = projector.to_utm(shape(sortie.track_geojson))
        survey_utm = projector.to_utm(shape(sortie.survey_tracks_geojson))
        all_survey_tracks_utm.extend(
            list(survey_utm.geoms) if survey_utm.geom_type == "MultiLineString" else [survey_utm]
        )

        geozone_results.append(check_geozones(route_utm, no_fly_footprints, obstacle_footprints))

        if allowed_union is None:
            airspace_results.append(CheckResult("airspace", False, (
                Violation("в обстановке нет ни одной зоны разрешенного воздушного пространства", Point(route_utm.coords[0])),
            )))
        else:
            airspace_results.append(check_allowed_space(route_utm, allowed_union))

        energy_results.append(
            check_energy(route_utm.length, plan.cruise_speed_mps, plan.budget_s, route=route_utm)
        )
        reachability_results.append(
            check_reachability(route_utm, landing_points, plan.cruise_speed_mps, plan.budget_s)
        )
        daylight_results.append(
            check_daylight(
                sortie.start_utc, sortie.end_utc, lat, lon, window_start_hour, window_end_hour,
                location=Point(route_utm.coords[0]),
            )
        )
        sortie_tracks.append(SortieTrack(
            uav_id=sortie.uav_id, route=route_utm,
            start_utc=sortie.start_utc, end_utc=sortie.end_utc,
            cruise_speed_mps=plan.cruise_speed_mps,
        ))

    try:
        working_area = compute_working_area(
            projector.to_utm(area_geom), allowed_zones, no_fly_zones, obstacles, plan.height_m
        )
        coverage_result = check_coverage(all_survey_tracks_utm, working_area, plan.swath_m)
    except GeometryError as exc:
        coverage_result = CheckResult("coverage", False, (Violation(f"не удалось пересчитать рабочую область: {exc}"),))

    separation_result = check_separation(sortie_tracks)

    return [
        _combine("geozones", geozone_results, projector),
        _combine("airspace", airspace_results, projector),
        _combine("energy", energy_results, projector),
        _combine("reachability", reachability_results, projector),
        SafetyCheckOut(
            name="coverage", label=_LABELS["coverage"], passed=coverage_result.passed,
            violations=[_violation_out(v, projector) for v in coverage_result.violations],
        ),
        _combine("daylight", daylight_results, projector),
        SafetyCheckOut(
            name="separation", label=_LABELS["separation"], passed=separation_result.passed,
            violations=[_violation_out(v, projector) for v in separation_result.violations],
        ),
    ]


def _load_context(plan_id: str):
    plan = plan_service.get_plan(plan_id)
    task = task_service.get_task(plan.task_id)
    try:
        env = environment_service.get_environment(task.environment_id)
    except KeyError:
        raise SafetyCheckError("обстановка задачи не найдена — проверка невозможна")
    return plan, task, env


def _status_of(checks: list[SafetyCheckOut]) -> str:
    return "Пройдена" if all(c.passed for c in checks) else "Есть нарушения"


def _store_report(original_plan_id: str, plan: PlanDetail, task, checks: list[SafetyCheckOut], attempts: int) -> SafetyReport:
    report = SafetyReport(
        id=str(uuid.uuid4()), plan_id=plan.id, task_id=task.id,
        created_at=datetime.now(timezone.utc), status=_status_of(checks),
        checks=checks, auto_recalc_count=attempts,
    )
    _reports_by_plan.setdefault(original_plan_id, []).append(report)
    if plan.id != original_plan_id:
        _reports_by_plan.setdefault(plan.id, []).append(report)
    return report


def check_plan(plan_id: str) -> SafetyReport:
    """БЕЗ.ФТ.1 + БЕЗ.ФТ.3: полная проверка плана и, при нарушении, до трех
    автоматических пересчетов подряд в модуле «Планирование» — без участия
    оператора, в рамках одного вызова.

    v1-ограничение: автоматический пересчет здесь — это повторный вызов
    ``plan_service.create_plan()`` без каких-либо скорректированных
    параметров (адаптация «тип нарушения → корректировка запроса», описанная
    в БЕЗ.ФТ.3 и концепции решения, раздел 4Г, не реализована — расчетное
    ядро детерминировано и не принимает подсказок от проверки). На
    неизменной задаче повтор почти всегда воспроизводит то же нарушение, и
    три попытки, как правило, расходуются впустую. Счетчик и итоговое
    ограничение в три попытки при этом соблюдаются честно: оператор
    получает достоверную историю попыток, а не имитацию улучшения.
    """
    original_plan_id = plan_id
    plan, task, env = _load_context(plan_id)
    key = (task.id, task.version)
    attempts = _attempts_by_task_version.get(key, 0)

    checks = _run_checks(env, task, plan)
    while _status_of(checks) == "Есть нарушения" and attempts < MAX_AUTO_RECALC:
        attempts += 1
        _attempts_by_task_version[key] = attempts
        new_summary = plan_service.create_plan(task.id)
        plan = plan_service.get_plan(new_summary.id)
        checks = _run_checks(env, task, plan)

    return _store_report(original_plan_id, plan, task, checks, attempts)


def recheck_plan(plan_id: str) -> SafetyReport:
    """БЕЗ.ФТ.6 «Повторить проверку» — повторный вызов проверки на текущей
    версии плана без ее пересчета (например, после того как оператор устранил
    причину нарушения в другом месте — обстановке или парке — не трогая
    саму задачу и не запуская новый расчет)."""
    plan, task, env = _load_context(plan_id)
    checks = _run_checks(env, task, plan)
    key = (task.id, task.version)
    attempts = _attempts_by_task_version.get(key, 0)
    return _store_report(plan_id, plan, task, checks, attempts)


def get_latest_report(plan_id: str) -> SafetyReport:
    reports = _reports_by_plan.get(plan_id)
    if not reports:
        raise KeyError(f"для плана {plan_id} еще не выполнялась проверка безопасности")
    return reports[-1]
