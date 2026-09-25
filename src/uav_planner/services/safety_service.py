"""Оркестрация модуля «Проверка безопасности» — независимая от расчетного
ядра проверка готового плана. См. docs/trebovania/Проверка_безопасности.md
(БЕЗ.ФТ.1-6) и uav_planner.safety (восемь чистых проверок).

Модуль сознательно не переиспользует внутренние объекты ``plan_service``
(зоны, рабочую область) — заново разбирает слои обстановки из тех же исходных
данных, что и «Планирование», но собственным кодом. Так ошибка в допущениях
или парсинге ядра не повторится незамеченной в проверке (см. «контракт между
ролями» плана реализации: проверку пишет роль «И», независимо от ядра).
Общие с «Планированием» здесь только разбор интервалов действия зон
(``environment_service.parse_time_windows`` — формат данных обстановки,
а не допущение ядра) и формула светового дня (``schedule.work_window_utc``).

Интервалы действия зон проверка, в отличие от планирования, сверяет точно —
с фактическим временем каждого вылета: разрешенная зона засчитывается,
только если действует весь вылет, БПЗ — если действует хотя бы часть его.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.camera import CAMERA_SPECS, DEFAULT_MAX_ALTITUDE_M, max_gsd_for_height
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
    DEFAULT_ALTITUDE_STEP_M,
    CheckResult,
    SortieTrack,
    Violation,
    check_allowed_space,
    check_coverage,
    check_daylight,
    check_energy,
    check_geozones,
    check_max_altitude,
    check_reachability,
    check_separation,
    discretize,
)
from uav_planner.terrain import ElevationLookupError

from . import plan_service
from . import environment_service
from . import task_service
from . import terrain_service
from uav_planner.api.schemas.plan import PlanDetail
from uav_planner.api.schemas.safety import SafetyCheckOut, SafetyReport, ViolationOut

log = logging.getLogger(__name__)

_LABELS = {
    "geozones": "Геозоны",
    "airspace": "Разрешенное пространство",
    "altitude": "Максимальная высота",
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


def _agl_by_point(
    sample_points_utm: list[Point], projector: Projector, progress: ProgressReporter
) -> list[tuple[Point, float]] | None:
    """Независимо перепроверяет высоту над рельефом в точках маршрута —
    свой запрос к провайдеру высот, не переиспользующий профиль, который
    уже посчитал ``plan_service`` при встраивании рельефа в маршрут (тот же
    принцип независимости, что у остальных проверок этого модуля).

    ``None`` — рельеф не проверяем отдельно (выключен, точек нет, или
    провайдер недоступен): ``check_max_altitude`` тогда честно деградирует
    к сравнению одного числа ``plan.height_m``, а не молча пропускает
    проверку."""
    if not sample_points_utm:
        return None
    # Самая долгая часть проверки: на сцене в сотню вылетов — сотня запросов
    # по лимиту 1/с, поэтому каждый запрос тикает фоновой работе.
    provider = terrain_service.default_elevation_provider(on_request=progress.tick)
    if provider is None:
        return None

    points_wgs84 = [projector.to_wgs84(Point(p.x, p.y)).coords[0] for p in sample_points_utm]
    try:
        ground_m = provider.elevations(points_wgs84)
    except ElevationLookupError:
        log.warning("независимая проверка рельефа недоступна — высота сверяется как абсолютная")
        return None
    return [(p, p.z - g) for p, g in zip(sample_points_utm, ground_m)]


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


def _altitude_recommendations(task, plan: PlanDetail, max_agl_m: float) -> list[str]:
    """Высота выше потолка: единственный параметр, который ее задает, — GSD
    задачи (H = GSD·f·N_w/s_w), и менять его вправе только оператор. Поэтому
    не автопересчет, а конкретное число: максимально допустимое GSD, при
    котором и номинальная высота, и наибольшее найденное превышение над
    рельефом (облет рельефа ограничен углом набора и местами отстает от
    поверхности) укладываются в потолок."""
    camera = CAMERA_SPECS.get(plan.camera_key)
    limit = DEFAULT_MAX_ALTITUDE_M
    if camera is None:
        return [f"Уменьшите требуемое GSD задачи, чтобы высота полета не превышала {limit:.0f} м."]
    excess_m = max(0.0, max_agl_m - plan.height_m)
    allowed_height_m = limit - excess_m
    if allowed_height_m <= 0:
        return [
            f"Превышение над рельефом на маршруте ({excess_m:.0f} м) само больше потолка {limit:.0f} м — "
            "уменьшением GSD не исправить: измените область облета."
        ]
    gsd_max = max_gsd_for_height(camera, allowed_height_m)
    terrain_note = (
        f" (номинальная высота не выше {allowed_height_m:.0f} м — с запасом на превышение над рельефом "
        f"до {excess_m:.0f} м, найденное на маршруте)"
        if excess_m >= 0.5 else ""
    )
    return [
        f"Уменьшите требуемое разрешение съемки: GSD {task.gsd_cm:g} → не более {gsd_max:g} см/пиксель — "
        f"максимально допустимое для камеры «{camera.name}» при ограничении высоты {limit:.0f} м{terrain_note}. "
        "Затем пересчитайте план."
    ]


def _geozone_recommendations(applied_extra_buffer_m: float) -> list[str]:
    step = get_settings().recalc_no_fly_buffer_step_m
    if applied_extra_buffer_m > 0:
        return [
            f"Автоматический пересчет уже перестроил маршрут с дополнительным буфером {applied_extra_buffer_m:g} м "
            "вокруг запретных зон и препятствий, но пересечение осталось: обход в пределах сетки поиска не найден "
            "(зона перекрывает путь целиком или проход слишком узкий). Измените площадку вылета или область облета "
            "либо границы зоны в обстановке.",
        ]
    return [
        "Добавьте буферное пространство вокруг запретной зоны (safety_buffer_m в обстановке) и пересчитайте план — "
        "маршрут будет перестроен в обход расширенной зоны. Автоматический пересчет делает это сам: "
        f"+{step:g} м к буферу за каждую попытку.",
    ]


_STATIC_RECOMMENDATIONS = {
    "airspace": [
        "Проверьте, что зоны разрешенного пространства покрывают маршрут на высоте полета и действуют "
        "в дату и окно работ; при необходимости измените область облета, окно работ или GSD (высоту).",
    ],
    "energy": [
        "Уменьшите область облета, добавьте БВС в парк или выберите модель с большим временем полета.",
    ],
    "reachability": [
        "Добавьте в обстановку резервную площадку посадки ближе к удаленной части маршрута.",
    ],
    "coverage": [
        "Непокрытые участки обычно лежат в узких проходах между запретными зонами: уменьшите буфер зон "
        "или разделите область облета.",
    ],
    "daylight": [
        "Сдвиньте дату или окно работ так, чтобы вылеты укладывались в световой день.",
    ],
    "separation": [
        "Расширьте окно работ, чтобы расписание могло развести вылеты по времени, или разнесите площадки "
        "вылета бортов.",
    ],
}


def _with_recommendations(
    checks: list[SafetyCheckOut], task, plan: PlanDetail, max_agl_m: float, applied_extra_buffer_m: float
) -> list[SafetyCheckOut]:
    result = []
    for check in checks:
        if check.passed:
            result.append(check)
            continue
        if check.name == "altitude":
            recommendations = _altitude_recommendations(task, plan, max_agl_m)
        elif check.name == "geozones":
            recommendations = _geozone_recommendations(applied_extra_buffer_m)
        else:
            recommendations = list(_STATIC_RECOMMENDATIONS.get(check.name, []))
        result.append(check.model_copy(update={"recommendations": recommendations}))
    return result


def _run_checks(
    env, task, plan: PlanDetail, applied_extra_buffer_m: float = 0.0, progress: ProgressReporter | None = None
) -> list[SafetyCheckOut]:
    progress = progress or ProgressReporter(None)
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
            active_windows=environment_service.parse_time_windows(f["properties"].get("active_windows")),
        )
        for i, f in enumerate(airspace_feats)
    ]
    no_fly_zones = [
        NoFlyZone(
            id=str(i), polygon=projector.to_utm(shape(f["geometry"])),
            safety_buffer_m=float(f["properties"].get("safety_buffer_m", 0.0)),
            active_windows=environment_service.parse_time_windows(f["properties"].get("active_windows")),
        )
        for i, f in enumerate(no_fly_feats)
    ]
    obstacles = [
        Obstacle(
            id=str(i), polygon=projector.to_utm(shape(f["geometry"])),
            height=HeightRange(float(f["properties"]["h_min"]), float(f["properties"]["h_max"])),
            safety_buffer_m=float(f["properties"].get("safety_buffer_m", 0.0)),
        )
        for i, f in enumerate(obstacle_feats)
    ]

    # Разрешенное пространство — только зоны, чей диапазон высот включает
    # высоту полета плана (раньше объединялись все зоны без учета высоты, и
    # полет над потолком зоны считался разрешенным).
    height_allowed_zones = [z for z in allowed_zones if z.height.contains(plan.height_m)]
    obstacle_footprints = [o.footprint() for o in obstacles if o.is_hole_at(plan.height_m)]
    landing_points = [projector.to_utm(shape(f["geometry"])) for f in launch_feats + reserve_feats]

    lat, lon = area_geom.centroid.y, area_geom.centroid.x
    tz = task_service.task_tzinfo(task)
    settings = get_settings()

    # Пара (подпись вылета, результат): подпись нужна сообщению БЕЗ.ФТ.4.
    geozone_results: list[tuple[str | None, CheckResult]] = []
    airspace_results: list[tuple[str | None, CheckResult]] = []
    energy_results: list[tuple[str | None, CheckResult]] = []
    reachability_results: list[tuple[str | None, CheckResult]] = []
    daylight_results: list[tuple[str | None, CheckResult]] = []
    sortie_tracks: list[SortieTrack] = []
    all_survey_tracks_utm: list[BaseGeometry] = []
    altitude_sample_points_utm: list[Point] = []

    for sortie in plan.sorties:
        progress.tick()
        label = sortie_label(sortie)
        route_utm = projector.to_utm(shape(sortie.track_geojson))
        survey_utm = projector.to_utm(shape(sortie.survey_tracks_geojson))
        all_survey_tracks_utm.extend(
            list(survey_utm.geoms) if survey_utm.geom_type == "MultiLineString" else [survey_utm]
        )
        if route_utm.has_z:
            altitude_sample_points_utm.extend(discretize(route_utm, DEFAULT_ALTITUDE_STEP_M))

        no_fly_footprints = [
            z.footprint() for z in no_fly_zones if z.is_active_during(sortie.start_utc, sortie.end_utc)
        ]
        geozone_results.append(
            (label, check_geozones(route_utm, no_fly_footprints, obstacle_footprints))
        )

        sortie_allowed = [
            z.polygon for z in height_allowed_zones if z.is_active_throughout(sortie.start_utc, sortie.end_utc)
        ]
        if not allowed_zones:
            airspace_results.append((None, CheckResult("airspace", False, (
                Violation(
                    "в обстановке нет ни одной зоны разрешенного воздушного пространства",
                    Point(route_utm.coords[0]),
                ),
            ))))
        elif not sortie_allowed:
            airspace_results.append((label, CheckResult("airspace", False, (
                Violation(
                    f"на высоте {plan.height_m:.0f} м в интервале вылета не действует ни одна зона "
                    "разрешенного воздушного пространства",
                    Point(route_utm.coords[0]),
                ),
            ))))
        else:
            airspace_results.append((label, check_allowed_space(route_utm, unary_union(sortie_allowed))))

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
                sortie.start_utc, sortie.end_utc, lat, lon,
                window_start=task.window_start, window_end=task.window_end, tz=tz,
                location=Point(route_utm.coords[0]),
            ),
        ))
        sortie_tracks.append(SortieTrack(
            uav_id=sortie.uav_id, route=route_utm,
            start_utc=sortie.start_utc, end_utc=sortie.end_utc,
            cruise_speed_mps=plan.cruise_speed_mps,
        ))

    # Каждый вылет выше сверен со световым днем своих суток — этого мало:
    # расписание, начатое не в дату работ (окно пусто на эту дату), проходило
    # поштучную проверку целиком, хотя работы уехали на полгода вперед.
    if plan.sorties:
        first = min(plan.sorties, key=lambda s: s.start_utc)
        first_day = first.start_utc.astimezone(tz).date() if tz is not None else first.start_utc.date()
        if first_day != task.work_date:
            daylight_results.append((sortie_label(first), CheckResult("daylight", False, (
                Violation(
                    f"план начинается {first_day:%d.%m.%Y}, а дата работ задачи — {task.work_date:%d.%m.%Y}: "
                    "в окно работ этой даты вылеты не укладываются",
                    Point(projector.to_utm(shape(first.track_geojson)).coords[0]),
                ),
            ))))

    try:
        working_area = compute_working_area(
            projector.to_utm(area_geom), allowed_zones, no_fly_zones, obstacles, plan.height_m
        )
        coverage_result = check_coverage(
            all_survey_tracks_utm, working_area, plan.swath_m, tolerance=settings.coverage_tolerance
        )
    except GeometryError as exc:
        coverage_result = CheckResult(
            "coverage", False, (Violation(f"не удалось пересчитать рабочую область: {exc}"),)
        )

    progress.tick()
    separation_result = check_separation(sortie_tracks, min_separation_m=settings.separation_distance_m)
    agl_by_point = _agl_by_point(altitude_sample_points_utm, projector, progress)
    altitude_result = check_max_altitude(plan.height_m, agl_by_point=agl_by_point)
    max_agl_m = max([plan.height_m] + [agl for _, agl in (agl_by_point or [])])

    return _with_recommendations([
        _combine("geozones", geozone_results, projector),
        _combine("airspace", airspace_results, projector),
        SafetyCheckOut(
            name="altitude", label=_LABELS["altitude"], passed=altitude_result.passed,
            violations=[
                _violation_out(v, projector, f"altitude__{i}")
                for i, v in enumerate(altitude_result.violations)
            ],
        ),
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
    ], task, plan, max_agl_m, applied_extra_buffer_m)


def _load_context(plan_id: str):
    plan = plan_service.get_plan(plan_id)
    task = task_service.get_task(plan.task_id)
    try:
        env = environment_service.get_environment(task.environment_id)
    except KeyError:
        raise SafetyCheckError("обстановка задачи не найдена — проверка невозможна")
    return plan, task, env


def _check_passed(checks: list[SafetyCheckOut], name: str) -> bool:
    return all(c.passed for c in checks if c.name == name)


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

    Корректировка запроса по типу нарушения (БЕЗ.ФТ.3, концепция, раздел
    4Г) пока реализована для одного типа: пересечение запретных зон — каждая
    такая попытка наращивает буфер вокруг БПЗ и препятствий на
    ``recalc_no_fly_buffer_step_m`` и перестраивает маршрут. Для остальных
    нарушений пересчет повторяет расчет без изменений — ядро
    детерминировано, и повтор обычно воспроизводит то же нарушение; что
    сделать оператору, говорят рекомендации отчета
    (``SafetyCheckOut.recommendations``). Высоту выше потолка автопересчет не
    исправит в принципе: ее задает GSD задачи, и рекомендация называет
    максимально допустимое значение.

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
    checks = _run_checks(env, task, plan, progress=progress)
    buffer_step_m = get_settings().recalc_no_fly_buffer_step_m
    extra_buffer_m = 0.0
    while _status_of(checks) == "Есть нарушения" and attempts < max_recalc:
        attempts += 1
        # БЕЗ.ФТ.3: скорректированный запрос по типу нарушения. Пересечение
        # запретной зоны — запас вокруг БПЗ и препятствий растет на шаг с
        # каждой такой попыткой, и маршрут перестраивается от расширенных
        # контуров. Запас сохраняется и в следующих попытках, даже если они
        # вызваны другим нарушением: иначе пересчет вернул бы прежний маршрут.
        if not _check_passed(checks, "geozones"):
            extra_buffer_m += buffer_step_m
        repositories.safety.set_attempts(task.id, task.version, attempts)
        log.info(
            "автоматический пересчет после нарушения",
            extra={"attempt": attempts, "max_attempts": max_recalc, "plan_id": plan.id},
        )
        progress.publish(
            f"В процессе автоматического пересчета ({attempts} из {max_recalc})",
            int(40 + 50 * attempts / (max_recalc + 1)),
        )
        new_summary = plan_service.create_plan(
            task.id, extra_no_fly_buffer_m=extra_buffer_m, progress=progress.quiet()
        )
        plan = plan_service.get_plan(new_summary.id)
        checks = _run_checks(env, task, plan, applied_extra_buffer_m=extra_buffer_m, progress=progress)

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
    checks = _run_checks(env, task, plan, progress=progress)
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


def set_all_violations_ignored(report_id: str, ignored: bool) -> SafetyReport:
    """Кнопка «Игнорировать все нарушения» (по запросу пользователя) — отмечает
    принятыми сразу все нарушения отчёта, не по одному. То же расширение
    поверх ЭКС.ФТ.2, что и ``set_violation_ignored``: подтверждение плана
    разблокируется, только когда отмечено действительно каждое нарушение —
    массовая отметка — просто быстрый способ дойти до этого состояния, а не
    отдельное правило."""
    updated = repositories.safety.set_all_violations_ignored(report_id, ignored)
    if updated is None:
        raise ViolationNotFoundError(f"в отчете {report_id} нет нарушений")
    log.info(
        "отметка принятия нарушений изменена для всего отчета",
        extra={
            "report_id": report_id, "ignored": ignored,
            "violations_acknowledged": updated.violations_acknowledged,
        },
    )
    return updated
