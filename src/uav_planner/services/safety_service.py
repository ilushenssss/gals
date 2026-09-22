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

import logging
import uuid
from datetime import datetime, timezone

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.config import get_settings
from uav_planner.domain.errors import GalsError
from uav_planner.geometry import (
    AllowedZone,
    GeometryError,
    HeightRange,
    NoFlyZone,
    Obstacle,
    Projector,
    compute_working_area,
)
from uav_planner.jobs.progress import ProgressReporter
from uav_planner.logging_setup import log_context
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
from . import environment_service
from . import task_service
from uav_planner.api.schemas.plan import PlanDetail
from uav_planner.api.schemas.safety import SafetyCheckOut, SafetyReport, ViolationOut

log = logging.getLogger(__name__)

_LABELS = {
    "geozones": "Геозоны",
    "airspace": "Разрешенное пространство",
    "energy": "Энергия",
    "reachability": "Достижимость площадки",
    "coverage": "Покрытие",
    "daylight": "Световой день",
    "separation": "Разведение",
}



class SafetyCheckError(GalsError, ValueError):
    """Проверку невозможно выполнить (например, план ссылается на удаленную обстановку)."""


def _valid_features(env, layer: str) -> list[dict]:
    return [f for f in env.layers.get(layer, []) if f.get("properties", {}).get("_valid", True)]


def _time_to_hours(t) -> float | None:
    return None if t is None else t.hour + t.minute / 60.0 + t.second / 3600.0


def sortie_label(sortie) -> str:
    """Подпись вылета для сообщения о нарушении.

    БЕЗ.ФТ.4 требует показать при нарушении идентификатор БВС и вылета, а
    чистые проверки его не знают: они работают с геометрией и возвращают
    только причину. Приписывает подпись оркестратор — он единственный, кто
    видит, чей это вылет. Заодно это делает нарушение адресуемым на карте
    (ИНТ.ФТ.14): по подписи фронтенд находит маршрут.
    """
    return f"{sortie.uav_id} · вылет {sortie.sortie_index + 1}"


def _violation_out(v: Violation, projector: Projector, violation_id: str) -> ViolationOut:
    """Нарушение ядра -> нарушение контракта API: точка перепроецируется из
    UTM в WGS-84, чтобы интерфейс мог поставить маркер на карту."""
    if v.point is None:
        return ViolationOut(id=violation_id, message=v.message)
    wgs = projector.to_wgs84(v.point)
    return ViolationOut(id=violation_id, message=v.message, lat=wgs.y, lon=wgs.x)


def _combine(
    name: str, results: list[tuple[str | None, CheckResult]], projector: Projector
) -> SafetyCheckOut:
    """Сводит результаты по всем вылетам в одну проверку отчёта.

    Подпись вылета приписывается к тексту нарушения здесь же: чистые проверки
    работают с геометрией и не знают, чей это вылет (БЕЗ.ФТ.4). Координата при
    этом остаётся своя у каждого нарушения — по ней и ставится маркер.
    """
    labelled: list[Violation] = []
    for label, r in results:
        for v in r.violations:
            labelled.append(Violation(f"{label}: {v.message}" if label else v.message, v.point))

    unique: dict[str, Violation] = {}
    for v in labelled:
        unique.setdefault(v.message, v)
    shown = list(unique.values())[:5]
    if len(unique) > 5:
        shown.append(Violation(f"...и еще {len(unique) - 5} нарушени(й)"))
    passed = all(r.passed for _, r in results) if results else True
    return SafetyCheckOut(
        name=name, label=_LABELS[name], passed=passed,
        violations=[_violation_out(v, projector, f"{name}__{i}") for i, v in enumerate(shown)],
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

    # Пара (подпись вылета, результат): подпись нужна сообщению БЕЗ.ФТ.4.
    geozone_results: list[tuple[str | None, CheckResult]] = []
    airspace_results: list[tuple[str | None, CheckResult]] = []
    energy_results: list[tuple[str | None, CheckResult]] = []
    reachability_results: list[tuple[str | None, CheckResult]] = []
    daylight_results: list[tuple[str | None, CheckResult]] = []
    sortie_tracks: list[SortieTrack] = []
    all_survey_tracks_utm: list[BaseGeometry] = []

    for sortie in plan.sorties:
        label = sortie_label(sortie)
        route_utm = projector.to_utm(shape(sortie.track_geojson))
        survey_utm = projector.to_utm(shape(sortie.survey_tracks_geojson))
        all_survey_tracks_utm.extend(
            list(survey_utm.geoms) if survey_utm.geom_type == "MultiLineString" else [survey_utm]
        )

        geozone_results.append(
            (label, check_geozones(route_utm, no_fly_footprints, obstacle_footprints))
        )

        if allowed_union is None:
            airspace_results.append((None, CheckResult("airspace", False, (
                Violation(
                    "в обстановке нет ни одной зоны разрешенного воздушного пространства",
                    Point(route_utm.coords[0]),
                ),
            ))))
        else:
            airspace_results.append((label, check_allowed_space(route_utm, allowed_union)))

        energy_results.append(
            (label, check_energy(route_utm.length, plan.cruise_speed_mps, plan.budget_s, route=route_utm))
        )
        reachability_results.append((
            label,
            check_reachability(route_utm, landing_points, plan.cruise_speed_mps, plan.budget_s),
        ))
        daylight_results.append((
            label,
            check_daylight(
                sortie.start_utc, sortie.end_utc, lat, lon, window_start_hour, window_end_hour,
                location=Point(route_utm.coords[0]),
            ),
        ))
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
        coverage_result = CheckResult(
            "coverage", False, (Violation(f"не удалось пересчитать рабочую область: {exc}"),)
        )

    separation_result = check_separation(sortie_tracks)

    return [
        _combine("geozones", geozone_results, projector),
        _combine("airspace", airspace_results, projector),
        _combine("energy", energy_results, projector),
        _combine("reachability", reachability_results, projector),
        SafetyCheckOut(
            name="coverage", label=_LABELS["coverage"], passed=coverage_result.passed,
            violations=[
                _violation_out(v, projector, f"coverage__{i}")
                for i, v in enumerate(coverage_result.violations)
            ],
        ),
        _combine("daylight", daylight_results, projector),
        SafetyCheckOut(
            name="separation", label=_LABELS["separation"], passed=separation_result.passed,
            violations=[
                _violation_out(v, projector, f"separation__{i}")
                for i, v in enumerate(separation_result.violations)
            ],
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
    repositories.safety.add_report([original_plan_id, plan.id], report)
    # ЭКС.ФТ.5: «Проверен» означает, что проверка выполнена и результат
    # известен, — независимо от того, есть нарушения или нет. Подтверждение
    # блокирует не статус плана, а статус самого отчета (ЭКС.ФТ.2).
    repositories.plans.mark_checked(plan.id)
    with log_context(task_id=task.id, plan_id=plan.id):
        log.info(
            "отчет проверки безопасности сохранен",
            extra={
                "report_status": report.status,
                "auto_recalc_count": attempts,
                "failed_checks": [c.name for c in checks if not c.passed],
            },
        )
    return report


def check_plan(plan_id: str, progress: ProgressReporter | None = None) -> SafetyReport:
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

    ``progress`` — репортер фоновой работы. Именно он делает наблюдаемым
    третий статус БЕЗ.ФТ.5 «В процессе автоматического пересчета»: в
    синхронном вызове цикл целиком проходит внутри одного HTTP-запроса, и
    промежуточное состояние снаружи не видно в принципе. Без репортера
    (``None``) поведение функции прежнее.
    """
    progress = progress or ProgressReporter(None)
    original_plan_id = plan_id
    plan, task, env = _load_context(plan_id)
    attempts = repositories.safety.get_attempts(task.id, task.version)

    max_recalc = get_settings().max_auto_recalc
    progress.stage("safety_check")
    checks = _run_checks(env, task, plan)
    while _status_of(checks) == "Есть нарушения" and attempts < max_recalc:
        attempts += 1
        repositories.safety.set_attempts(task.id, task.version, attempts)
        log.info(
            "автоматический пересчет после нарушения",
            extra={"attempt": attempts, "max_attempts": max_recalc, "plan_id": plan.id},
        )
        progress.publish(
            f"В процессе автоматического пересчета ({attempts} из {max_recalc})",
            int(40 + 50 * attempts / (max_recalc + 1)),
        )
        new_summary = plan_service.create_plan(task.id)
        plan = plan_service.get_plan(new_summary.id)
        checks = _run_checks(env, task, plan)

    progress.stage("safety_save")
    return _store_report(original_plan_id, plan, task, checks, attempts)


def recheck_plan(plan_id: str, progress: ProgressReporter | None = None) -> SafetyReport:
    """БЕЗ.ФТ.6 «Повторить проверку» — повторный вызов проверки на текущей
    версии плана без ее пересчета (например, после того как оператор устранил
    причину нарушения в другом месте — обстановке или парке — не трогая
    саму задачу и не запуская новый расчет).

    Счетчик автопересчетов здесь только читается — увеличивать его повторная
    проверка не должна (БЕЗ.ФТ.3: лимит на автоматические пересчеты, а не на
    ручные проверки)."""
    progress = progress or ProgressReporter(None)
    plan, task, env = _load_context(plan_id)
    progress.stage("safety_check")
    checks = _run_checks(env, task, plan)
    attempts = repositories.safety.get_attempts(task.id, task.version)
    progress.stage("safety_save")
    return _store_report(plan_id, plan, task, checks, attempts)


def get_report(report_id: str) -> SafetyReport:
    """Отчет по идентификатору — так фоновая работа отдает свой результат."""
    return repositories.safety.get_report(report_id)


def get_latest_report(plan_id: str) -> SafetyReport:
    report = repositories.safety.latest_report(plan_id)
    if report is None:
        raise KeyError(f"для плана {plan_id} еще не выполнялась проверка безопасности")
    return report


class ViolationNotFoundError(KeyError):
    """Нарушения с таким id в отчёте нет — например, ссылка устарела: отчёт
    пересобирается при каждой проверке, и прежние идентификаторы не
    сохраняются (см. ``set_violation_ignored``)."""


def set_violation_ignored(report_id: str, violation_id: str, ignored: bool) -> SafetyReport:
    """Оператор осознанно принимает риск конкретного нарушения (или снимает
    отметку).

    Расширение поверх ЭКС.ФТ.2 по запросу пользователя: план с нарушениями
    подтвердить нельзя, но если оператор отметил принятым КАЖДОЕ нарушение
    отчёта, подтверждение разблокируется (``SafetyReport.violations_acknowledged``).

    Область действия — конкретный отчёт, а не план или его версия: новый
    расчёт и повторная проверка (БЕЗ.ФТ.3/ФТ.6) строят отчёт заново, и отметки
    не переносятся. Это решение, а не недоработка: принятый риск для одних
    условий не должен молча считаться принятым для другого результата.
    """
    updated = repositories.safety.set_violation_ignored(report_id, violation_id, ignored)
    if updated is None:
        raise ViolationNotFoundError(
            f"нарушение {violation_id} не найдено в отчете {report_id}"
        )
    log.info(
        "отметка принятия нарушения изменена",
        extra={
            "report_id": report_id, "violation_id": violation_id, "ignored": ignored,
            "violations_acknowledged": updated.violations_acknowledged,
        },
    )
    return updated
