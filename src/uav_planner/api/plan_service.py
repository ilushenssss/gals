"""Оркестрация расчета плана — первая версия модуля «Планирование».

Пайплайн: Environment + Task + Fleet -> для каждой подходящей по нагрузке
группы (модель БВС, камера) строится отдельный кандидат плана — рабочая
область (``geometry``) -> высота съемки и шаг галсов (``camera``) ->
декомпозиция и галсы (``coverage``) -> кластеризация по площадкам БВС +
TSP-тур внутри кластера + балансировка узкого места + разбиение на вылеты по
бюджету (``routing.cluster_assign_and_route`` — адаптация схемы «взвешенный
K-Means + m-TSP + балансировка», см. docstring модуля) -> точный пересчет
налета каждого вылета по фактическому маршруту в обход зон
(``visibility.find_path``) -> расписание (``schedule``, световой день, уже по
точному налету) -> метрики J1/J2 -> из всех кандидатов выбирается тот, что
минимизирует критерий задачи (ПЛН.ФТ.2, см. ``_pick_best_candidate``) -> ``Plan``.

См. docs/trebovania/Планирование.md (ПЛН.ФТ.1-10) и docs/trebovania/
Математическая_модель.md. Известные упрощения первой версии — см.
``main/README.md``, раздел про модуль «Планирование»:
  - одна модель БВС на план (смешанный парк в одной задаче — будущая
    версия) — но какая именно модель, решает не эвристика «больше готовых
    экземпляров», а честное сравнение: для КАЖДОЙ подходящей по нагрузке
    группы (модель, камера) строится полный кандидат плана, и побеждает тот,
    что минимизирует критерий задачи — нормированная взвешенная сумма
    J = α·J1/J1* + (1-α)·J2/J2* (Математическая_модель.md, раздел 13,
    см. ``_pick_best_candidate``);
  - крейсерская скорость = паспортный максимум модели минус скорость ветра
    задачи (без учета направления — консервативная оценка, до полной модели
    ветра в модуле ``motion``);
  - переходы между галсами и до площадки учтены в бюджете вылета
    (``routing.cluster_assign_and_route``, кластеризация + TSP-тур + Split) и
    в самом налете — но при кластеризации/маршрутизации переходы оцениваются
    по прямой (дешево, без A* на каждую пару кандидатов); фактический
    маршрут в обход зон (``uav_planner.visibility.find_path``) строится уже
    после маршрутизации и обычно чуть длиннее прямой — именно по нему
    пересчитывается точный налет каждого вылета перед расписанием (см.
    ``_build_sortie_legs``). Обход не гарантирован в редких случаях (слишком
    узкий проход для разрешения сетки — тогда используется прямая линия);
    порядок галсов внутри вылета теперь оптимизируется под минимальный
    транзит (TSP-эвристика — ближайший сосед + 2-opt), но не идеально
    (эвристика, не точный солвер);
  - площадка старта/посадки — своя у каждого БВС (``_instance_launch_point``):
    собственная локация экземпляра (``FleetInstance.location_lat/lon``), если
    задана в парке, иначе ближайшая к области облета ВПП обстановки, иначе
    центроид рабочей области. Разные БВС одного плана естественно летают с
    разных площадок, если физически там стоят — отдельного шага «выбрать одну
    площадку на весь план» нет;
  - расчет синхронный (весь пайплайн выполняется в теле HTTP-запроса, один
    раз на каждого кандидата) — для сцен тестового масштаба (T1-T9) счет идет
    доли секунды; фоновый расчет с 30-минутным лимитом (сцены T11) — будущая
    версия;
  - результат независимо перепроверяется модулем «Проверка безопасности»
    (``uav_planner.safety``) отдельным вызовом API, а не в этом пайплайне.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner.camera import CAMERA_SPECS, CameraError, SurveyGeometry, plan_survey_geometry
from uav_planner.coverage import boustrophedon_cells, generate_tracks, split_long_track
from uav_planner.fleet import FLEET_MODELS, UavModelSpec, flight_time_budget_s
from uav_planner.geometry import (
    AllowedZone,
    GeometryError,
    HeightRange,
    NoFlyZone,
    Obstacle,
    Projector,
    compute_working_area,
    validate_polygon,
)
from uav_planner.routing import Sortie, Track, Vehicle, cluster_assign_and_route
from uav_planner.schedule import ScheduleError, assign_timestamps
from uav_planner.visibility import find_path

from . import fleet_service
from . import service as environment_service
from . import task_service
from .plan_models import PlanDetail, PlanSortie, PlanSortiePhase, PlanSummary

_plans: dict[str, PlanDetail] = {}
_plan_ids_by_task: dict[str, list[str]] = {}

SPECTRUM_BY_SURVEY_TYPE = {
    "RGB": "rgb",
    "мультиспектральная": "multispectral",
    "ИК": "thermal",
}

SORTIE_PENALTY_S = 60.0  # λ в J2 — вес одного вылета (износ на взлете/посадке)
MIN_EFFECTIVE_SPEED_MPS = 1.0

_SUMMARY_ONLY_EXCLUDE = {"sorties"}


class PlanInfeasibleError(ValueError):
    """Задачу невозможно рассчитать в текущем виде (ПЛН.ФТ.10)."""


def _to_summary(detail: PlanDetail) -> PlanSummary:
    return PlanSummary(**detail.model_dump(exclude=_SUMMARY_ONLY_EXCLUDE))


def _valid_features(env, layer: str) -> list[dict]:
    return [f for f in env.layers.get(layer, []) if f.get("properties", {}).get("_valid", True)]


def _time_to_hours(t: time | None) -> float | None:
    return None if t is None else t.hour + t.minute / 60.0 + t.second / 3600.0


def _survey_type_spectrum(survey_type: str) -> str:
    spectrum = SPECTRUM_BY_SURVEY_TYPE.get(survey_type)
    if spectrum is None:
        raise PlanInfeasibleError(
            f"тип съемки «{survey_type}» пока не поддерживается расчетным ядром "
            "(LiDAR и геофизическая съемка — модельные профили, будущая версия)"
        )
    return spectrum


def _group_by_model_camera(eligible: list, spectrum: str) -> dict[tuple[str, str], list]:
    """ПЛН.ФТ.10-подготовка: группирует готовые экземпляры по (модель, совместимая
    камера) — по одной группе на кандидата плана; какая группа в итоге побеждает,
    решает ``_pick_best_candidate`` по критерию задачи, а не размер группы."""
    groups: dict[tuple[str, str], list] = {}
    for inst in eligible:
        model = FLEET_MODELS.get(inst.model_key)
        if model is None:
            continue
        camera_key = next(
            (c for c in model.compatible_cameras if CAMERA_SPECS[c].spectrum == spectrum), None
        )
        if camera_key is None:
            continue
        groups.setdefault((inst.model_key, camera_key), []).append(inst)
    return groups


def _nearest_site(point: Point, site_feats: list[dict], projector: Projector) -> tuple[Point, str] | None:
    """Ближайшая к ``point`` (UTM) точка среди фич слоя площадок — не первая
    попавшаяся, а действительно ближайшая."""
    best: tuple[float, Point, str] | None = None
    for f in site_feats:
        site_point = projector.to_utm(shape(f["geometry"]))
        d = point.distance(site_point)
        if best is None or d < best[0]:
            best = (d, site_point, f["properties"].get("name") or "ВПП")
    return (best[1], best[2]) if best else None


def _instance_launch_point(
    inst, launch_feats: list[dict], area_centroid: Point, projector: Projector
) -> tuple[Point, str | None]:
    """Площадка вылета/посадки конкретного экземпляра БВС — у разных БВС парка
    она может быть разной (ПЛН.ФТ.2): собственная локация экземпляра
    (``FleetInstance.location_lat/lon``), если задана в парке; иначе —
    ближайшая к области облета ВПП обстановки (не первая попавшаяся); если
    ВПП в обстановке нет вовсе — центроид области облета."""
    if inst.location_lat is not None and inst.location_lon is not None:
        point = projector.to_utm(Point(inst.location_lon, inst.location_lat))
        return point, inst.base_launch_site
    if launch_feats:
        nearest = _nearest_site(area_centroid, launch_feats, projector)
        if nearest:
            return nearest
    return area_centroid, None


_MIN_LEG_LENGTH_M = 1.0  # переходы короче этого не показываем отдельным этапом расписания


@dataclass(frozen=True)
class RouteLeg:
    """Один физический этап вылета — переход (обходит запрещенные зоны через
    ``visibility.find_path``) или сам галс. Используется дважды: чтобы честно
    посчитать налет вылета (переходы входят в бюджет, см. ``routing.cluster.
    cluster_assign_and_route``) и чтобы построить подробное расписание по
    вылету (ИНТ.ФТ.15 — клик по пункту расписания на Экране 4)."""

    kind: str  # "transit" | "survey"
    label: str
    coords: list[tuple[float, float]]  # UTM, включает обе граничные точки
    length_m: float
    fallback: bool = False


def _build_sortie_legs(
    sortie: Sortie, start_point: Point, launch_name: str | None, restricted_zones: list[BaseGeometry]
) -> list[RouteLeg]:
    """Раскладывает вылет на этапы: взлет и перелет до зоны выполнения
    задания, каждый галс, переходы между галсами, возврат и посадка.
    Переходы обходят ``restricted_zones`` (бесполетные зоны и высотные
    препятствия) через сеточный A* (``visibility.find_path``), если прямая
    линия их пересекает; иначе используется прямая. Сами галсы не
    проверяются — они лежат в рабочей области, уже построенной без этих зон.

    Сумма ``length_m`` всех этапов — это и есть фактический налет вылета
    (используется для пересчета ``Sortie.flight_time_s`` перед расписанием,
    см. ``create_plan``): переходы в нем учтены, а не только галсы."""
    legs: list[RouteLeg] = []
    current = start_point
    takeoff_label = (
        f"Взлет с площадки «{launch_name}» и перелет до зоны выполнения задания"
        if launch_name else "Взлет и перелет до зоны выполнения задания"
    )
    landing_label = f"Возврат и посадка на площадке «{launch_name}»" if launch_name else "Возврат на площадку"

    for idx, track in enumerate(sortie.tracks):
        line_coords = list(track.geometry.coords)
        start_pt, end_pt = line_coords[0], line_coords[-1]
        if current.distance(Point(end_pt)) < current.distance(Point(start_pt)):
            line_coords = line_coords[::-1]
        next_point = Point(line_coords[0])

        transit = find_path(current, next_point, restricted_zones)
        if transit.line.length > _MIN_LEG_LENGTH_M:
            label = takeoff_label if idx == 0 else f"Переход к галсу {idx + 1}"
            legs.append(RouteLeg("transit", label, list(transit.line.coords), transit.line.length, transit.fallback))

        legs.append(RouteLeg("survey", f"Галс {idx + 1}", line_coords, LineString(line_coords).length))
        current = Point(line_coords[-1])

    transit = find_path(current, start_point, restricted_zones)
    if transit.line.length > _MIN_LEG_LENGTH_M:
        legs.append(RouteLeg("transit", landing_label, list(transit.line.coords), transit.line.length, transit.fallback))

    return legs


@dataclass
class _Candidate:
    """Полностью рассчитанный план для одной группы (модель, камера) —
    один из нескольких, между которыми выбирает ``_pick_best_candidate``."""

    model_key: str
    camera_key: str
    model: UavModelSpec
    survey_geometry: SurveyGeometry
    cruise_speed: float
    budget_s: float
    plan_sorties: list[PlanSortie]
    j1_s: float
    j2_s: float
    warnings: list[str]


def _pick_best_candidate(candidates: list[_Candidate], criterion_alpha: float) -> _Candidate:
    """ПЛН.ФТ.2: если задаче подходит несколько моделей/камер, выбирается та,
    что минимизирует критерий задачи — нормированная взвешенная сумма
    J = α·J1/J1* + (1-α)·J2/J2* (Математическая_модель.md, раздел 13); J1*/J2*
    — лучшие (минимальные) значения среди уже посчитанных кандидатов, а не
    какие-то теоретические оптимумы. При единственном кандидате сравнивать
    не с чем — он и побеждает, деление не выполняется вовсе.

    ``criterion_alpha`` уже однозначно кодирует режим критерия задачи (1.0
    для «Время», 0.0 для «Налет», значение пользователя для «Компромисс»,
    см. ``task_service._validate``) — отдельно режим передавать не нужно."""
    if len(candidates) == 1:
        return candidates[0]

    j1_best = min(c.j1_s for c in candidates)
    j2_best = min(c.j2_s for c in candidates)

    def score(c: _Candidate) -> float:
        j1_term = (c.j1_s / j1_best) if j1_best > 0 else 0.0
        j2_term = (c.j2_s / j2_best) if j2_best > 0 else 0.0
        return criterion_alpha * j1_term + (1 - criterion_alpha) * j2_term

    return min(candidates, key=score)


def create_plan(task_id: str) -> PlanSummary:
    task = task_service.get_task(task_id)
    env = environment_service.get_environment(task.environment_id)
    if env.status != "Корректна":
        raise PlanInfeasibleError("обстановка задачи содержит ошибки и недоступна для расчета")

    spectrum = _survey_type_spectrum(task.survey_type)
    eligible = fleet_service.eligible_instances()
    if not eligible:
        raise PlanInfeasibleError("нет загруженного парка БВС в статусе «Готов»")

    groups = _group_by_model_camera(eligible, spectrum)
    if not groups:
        raise PlanInfeasibleError(
            f"нет готовых БВС с нагрузкой, совместимой с типом съемки «{task.survey_type}»"
        )

    area_geom = shape(task.area)
    airspace_feats = _valid_features(env, "airspace")
    no_fly_feats = _valid_features(env, "no_fly")
    obstacle_feats = _valid_features(env, "obstacle")
    launch_feats = _valid_features(env, "launch_site")

    all_geoms: list[BaseGeometry] = [area_geom]
    all_geoms += [shape(f["geometry"]) for f in airspace_feats + no_fly_feats + obstacle_feats + launch_feats]
    projector = Projector.for_geometry(unary_union(all_geoms))

    allowed_zones = [
        AllowedZone(
            id=str(i),
            polygon=projector.to_utm(shape(f["geometry"])),
            height=HeightRange(float(f["properties"]["h_min"]), float(f["properties"]["h_max"])),
        )
        for i, f in enumerate(airspace_feats)
    ]
    no_fly_zones = [
        NoFlyZone(
            id=str(i),
            polygon=projector.to_utm(shape(f["geometry"])),
            safety_buffer_m=float(f["properties"].get("safety_buffer_m", 0.0)),
        )
        for i, f in enumerate(no_fly_feats)
    ]
    obstacles = [
        Obstacle(
            id=str(i),
            polygon=projector.to_utm(shape(f["geometry"])),
            height=HeightRange(float(f["properties"]["h_min"]), float(f["properties"]["h_max"])),
        )
        for i, f in enumerate(obstacle_feats)
    ]

    validate_polygon(area_geom)
    area_utm = projector.to_utm(area_geom)
    area_centroid = area_utm.centroid

    # Площадка вылета/посадки — своя у каждого экземпляра БВС (не у плана в
    # целом), считается один раз для всех кандидатов: от группы (модель,
    # камера) она не зависит, только от самого экземпляра и обстановки.
    launch_by_inv: dict[str, tuple[Point, str | None]] = {
        inst.inventory_number: _instance_launch_point(inst, launch_feats, area_centroid, projector)
        for instances in groups.values()
        for inst in instances
    }

    lat, lon = area_geom.centroid.y, area_geom.centroid.x
    window_start_hour = _time_to_hours(task.window_start) or 0.0
    window_end_hour = _time_to_hours(task.window_end) or 24.0

    def build_candidate(model_key: str, camera_key: str, instances: list) -> _Candidate:
        model = FLEET_MODELS[model_key]
        try:
            survey_geometry = plan_survey_geometry(model_key, camera_key, task.gsd_cm)
        except CameraError as exc:
            raise PlanInfeasibleError(str(exc)) from exc

        # Запрещенные зоны для обхода на переходах (visibility.find_path) — те же
        # контуры, что compute_working_area вычитает из рабочей области: буфер
        # безопасности БПЗ и высотные препятствия, реально мешающие на высоте съемки.
        # Высота своя у каждого кандидата (зависит от камеры) — restricted_zones
        # и working_area нельзя переиспользовать между кандидатами.
        restricted_zones: list[BaseGeometry] = [zone.footprint() for zone in no_fly_zones] + [
            obstacle.footprint() for obstacle in obstacles if obstacle.is_hole_at(survey_geometry.height_m)
        ]
        try:
            working_area = compute_working_area(
                area_utm, allowed_zones, no_fly_zones, obstacles, survey_geometry.height_m
            )
        except GeometryError as exc:
            raise PlanInfeasibleError(str(exc)) from exc
        if working_area.is_empty:
            raise PlanInfeasibleError("рабочая область пуста на высоте съемки — нет свободного места для галсов")

        cells = boustrophedon_cells(working_area)
        max_route_m = model.max_route_km * 1000.0 if model.max_route_km else float("inf")

        raw_tracks: list[LineString] = []
        for cell in cells:
            for track in generate_tracks(cell, survey_geometry.track_spacing_m):
                raw_tracks.extend(split_long_track(track, max_route_m))
        if not raw_tracks:
            raise PlanInfeasibleError("рабочая область слишком мала для построения ни одного галса")

        wind_speed = task.wind_speed_ms or 0.0
        cruise_speed = max(model.speed_ms.max_ms - wind_speed, MIN_EFFECTIVE_SPEED_MPS)
        budget_s = flight_time_budget_s(model)

        vehicles = [
            Vehicle(
                id=inst.inventory_number, speed_mps=cruise_speed, budget_s=budget_s,
                launch_point=launch_by_inv[inst.inventory_number][0],
            )
            for inst in instances
        ]
        launch_name_by_vehicle = {inst.inventory_number: launch_by_inv[inst.inventory_number][1] for inst in instances}
        track_objs = [Track(id=f"track-{i}", geometry=t) for i, t in enumerate(raw_tracks)]
        # Кластеризация по площадкам + TSP-тур + балансировка узкого места
        # (routing.cluster) уже учитывают переходы при разбиении на вылеты
        # (по прямой) — но сумма ниже пересчитывает flight_time_s каждого
        # вылета точно, по фактическому маршруту в обход зон (visibility.find_path),
        # ДО построения расписания: даты/окна работ должны укладывать в световой
        # день настоящую, а не приблизительную длительность вылета.
        routing_result = cluster_assign_and_route(track_objs, vehicles)

        sortie_legs: dict[int, list[RouteLeg]] = {}
        any_transit_fallback = False
        for vehicle in vehicles:
            launch_name = launch_name_by_vehicle[vehicle.id]
            for sortie in routing_result.sorties_by_vehicle.get(vehicle.id, []):
                legs = _build_sortie_legs(sortie, vehicle.launch_point, launch_name, restricted_zones)
                sortie.flight_time_s = sum(leg.length_m for leg in legs) / cruise_speed
                sortie_legs[id(sortie)] = legs
                if any(leg.fallback for leg in legs):
                    any_transit_fallback = True

        plan_sorties: list[PlanSortie] = []
        total_flight_s = 0.0
        plan_start: datetime | None = None
        plan_end: datetime | None = None

        for vehicle in vehicles:
            launch_name = launch_name_by_vehicle[vehicle.id]
            sorties = routing_result.sorties_by_vehicle.get(vehicle.id, [])
            try:
                scheduled = assign_timestamps(
                    sorties, lat, lon, task.work_date,
                    window_start_hour=window_start_hour, window_end_hour=window_end_hour,
                )
            except ScheduleError as exc:
                raise PlanInfeasibleError(str(exc)) from exc

            for idx, sched in enumerate(scheduled):
                legs = sortie_legs[id(sched.sortie)]

                route_coords: list[tuple[float, float]] = []
                phases: list[PlanSortiePhase] = []
                cursor = sched.start_utc  # курсор передается от этапа к этапу, а не пересчитывается
                for leg in legs:                                     # из накопленной суммы секунд — иначе
                    route_coords.extend(leg.coords if not route_coords else leg.coords[1:])  # соседние этапы
                    phase_end = cursor + timedelta(seconds=leg.length_m / cruise_speed)       # могут разойтись
                    phases.append(PlanSortiePhase(                                            # на микросекунду
                        label=leg.label, kind=leg.kind, start_utc=cursor, end_utc=phase_end,
                        distance_m=leg.length_m,
                    ))
                    cursor = phase_end
                if phases:
                    # sched.end_utc посчитан отдельно (schedule.assign_timestamps, из суммы
                    # длин легов за один раз) — синхронизируем последний этап с ним явно,
                    # чтобы не осталось микросекундного расхождения от разного порядка
                    # операций с float.
                    phases[-1] = phases[-1].model_copy(update={"end_utc": sched.end_utc})

                route_line_utm = LineString(route_coords)
                route_line_wgs84 = projector.to_wgs84(route_line_utm)
                survey_tracks_wgs84 = projector.to_wgs84(
                    MultiLineString([t.geometry for t in sched.sortie.tracks])
                )
                # Фактическая длина маршрута целиком (переходы в обход зон + галсы),
                # а не только галсов — честнее отражает реальный налет по карте.
                distance_m = route_line_utm.length

                plan_sorties.append(PlanSortie(
                    uav_id=vehicle.id,
                    sortie_index=idx,
                    takeoff_site=launch_name,
                    landing_site=launch_name,
                    start_utc=sched.start_utc,
                    end_utc=sched.end_utc,
                    flight_time_s=sched.sortie.flight_time_s,
                    distance_m=distance_m,
                    track_geojson=mapping(route_line_wgs84),
                    survey_tracks_geojson=mapping(survey_tracks_wgs84),
                    phases=phases,
                ))
                total_flight_s += sched.sortie.flight_time_s
                plan_start = sched.start_utc if plan_start is None else min(plan_start, sched.start_utc)
                plan_end = sched.end_utc if plan_end is None else max(plan_end, sched.end_utc)

        j1_s = (plan_end - plan_start).total_seconds() if plan_start and plan_end else 0.0
        j2_s = total_flight_s + SORTIE_PENALTY_S * len(plan_sorties)

        warnings: list[str] = []
        if routing_result.unassigned_tracks:
            warnings.append(
                f"{len(routing_result.unassigned_tracks)} галс(ов) не удалось назначить ни одному БВС "
                "(превышают бюджет вылета любого кандидата) — увеличьте состав группы или используйте другую модель"
            )
        if any_transit_fallback:
            warnings.append(
                "на одном или нескольких переходах не удалось найти маршрут в обход бесполетной зоны/препятствия "
                "(слишком узкий проход для разрешения сетки поиска) — использована прямая линия; "
                "пересечение проверит модуль «Проверка безопасности»"
            )

        return _Candidate(
            model_key=model_key, camera_key=camera_key, model=model, survey_geometry=survey_geometry,
            cruise_speed=cruise_speed, budget_s=budget_s, plan_sorties=plan_sorties,
            j1_s=j1_s, j2_s=j2_s, warnings=warnings,
        )

    candidates: list[_Candidate] = []
    failures: list[str] = []
    for (model_key, camera_key), instances in groups.items():
        try:
            candidates.append(build_candidate(model_key, camera_key, instances))
        except PlanInfeasibleError as exc:
            failures.append(f"{FLEET_MODELS[model_key].name}: {exc}")

    if not candidates:
        raise PlanInfeasibleError(
            "ни одна из подходящих по нагрузке моделей БВС не позволяет рассчитать план: "
            + "; ".join(failures)
        )

    best = _pick_best_candidate(candidates, task.criterion_alpha)

    plan_id = str(uuid.uuid4())
    versions = _plan_ids_by_task.setdefault(task.id, [])
    detail = PlanDetail(
        id=plan_id,
        task_id=task.id,
        version=len(versions) + 1,
        created_at=datetime.now(timezone.utc),
        criterion_mode=task.criterion_mode,
        criterion_alpha=task.criterion_alpha,
        j1_s=best.j1_s,
        j2_s=best.j2_s,
        is_optimal=False,
        uav_model=best.model.name,
        sortie_count=len(best.plan_sorties),
        warnings=best.warnings,
        model_key=best.model_key,
        camera_key=best.camera_key,
        height_m=best.survey_geometry.height_m,
        swath_m=best.survey_geometry.swath_m,
        cruise_speed_mps=best.cruise_speed,
        budget_s=best.budget_s,
        sorties=best.plan_sorties,
    )
    _plans[plan_id] = detail
    versions.append(plan_id)
    task_service.mark_calculated(task.id)
    return _to_summary(detail)


def list_plans(task_id: str) -> list[PlanSummary]:
    ids = _plan_ids_by_task.get(task_id, [])
    return [_to_summary(_plans[i]) for i in reversed(ids)]


def get_plan(plan_id: str) -> PlanDetail:
    return _plans[plan_id]
