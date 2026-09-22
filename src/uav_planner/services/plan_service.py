"""Оркестрация расчета плана — первая версия модуля «Планирование».

Пайплайн: Environment + Task + Fleet -> рабочая область (``geometry``) ->
высота съемки и шаг галсов (``camera``) -> декомпозиция и галсы (``coverage``)
-> распределение и разбиение на вылеты (``routing``, LPT-эвристика) ->
расписание (``schedule``, световой день) -> метрики J1/J2 -> ``Plan``.

См. docs/trebovania/Планирование.md (ПЛН.ФТ.1-10) и docs/trebovania/
Математическая_модель.md. Известные упрощения первой версии — см.
``README.md``, раздел про модуль «Планирование»:
  - одна модель БВС на план — группа готовых экземпляров с совместимой
    нагрузкой, в которой больше всего экземпляров (смешанный парк в одной
    задаче — будущая версия);
  - крейсерская скорость = паспортный максимум модели минус скорость ветра
    задачи (без учета направления — консервативная оценка, до полной модели
    ветра в модуле ``motion``);
  - переходы между галсами и до площадки — прямые линии без учета препятствий
    (граф видимости — модуль ``visibility``, пока не реализован);
  - площадка старта/посадки — первая ВПП обстановки, иначе центроид рабочей
    области;
  - расчет не сохраняет промежуточное допустимое решение, поэтому остановка по
    лимиту времени (ПЛН.ФТ.3) фиксируется статусом, но отдать «лучшее из
    найденного» нечего — это появится вместе с решателем OR-Tools.

Расчет выполняется фоновой работой (``jobs/tasks.py``), а независимая проверка
результата — отдельный модуль «Проверка безопасности».
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.domain.errors import ConflictError, ValidationError
from uav_planner.domain.errors import PlanInfeasibleError as _DomainPlanInfeasibleError
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
from uav_planner.jobs.progress import CandidateProgress, ProgressReporter
from uav_planner.logging_setup import log_context
from uav_planner.routing import Sortie, Track, Vehicle, cluster_assign_and_route
from uav_planner.schedule import ScheduleError, assign_timestamps
from uav_planner.visibility import find_path

from . import fleet_service
from . import environment_service
from . import task_service
from uav_planner.api.schemas.plan import PlanDetail, PlanSortie, PlanSortiePhase, PlanSummary


SPECTRUM_BY_SURVEY_TYPE = {
    "RGB": "rgb",
    "мультиспектральная": "multispectral",
    "ИК": "thermal",
}

SORTIE_PENALTY_S = 60.0  # λ в J2 — вес одного вылета (износ на взлете/посадке)
MIN_EFFECTIVE_SPEED_MPS = 1.0

_SUMMARY_ONLY_EXCLUDE = {"sorties"}

log = logging.getLogger(__name__)


class PlanInfeasibleError(_DomainPlanInfeasibleError):
    """Задачу невозможно рассчитать в текущем виде (ПЛН.ФТ.10)."""


class PlanNotConfirmableError(ValidationError):
    """Предусловие ЭКС.ФТ.2 не выполнено — подтверждать нечего."""


class PlanConfirmConflictError(ConflictError):
    """ЭКС.ФТ.9: план уже подтвержден кем-то другим.

    Несет актуальную карточку плана: второму оператору нужно показать не
    только отказ, но и кто и когда подтвердил, и обновить интерфейс без
    повторного подтверждения.
    """

    def __init__(self, plan: PlanDetail):
        self.plan = plan
        who = plan.confirmed_by or "другой пользователь"
        if plan.status in ("Подтвержден", "Выгружен"):
            message = f"План уже подтвержден пользователем ({who})"
        else:
            message = (
                f"План нельзя подтвердить в статусе «{plan.status}» — "
                "сначала выполните проверку безопасности"
            )
        super().__init__(message)


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
    """Группы «модель БВС + совместимая камера» среди готовых экземпляров.

    Прежде отсюда возвращалась одна группа — самая многочисленная. Теперь
    возвращаются все: ПЛН.ФТ.2 требует выбрать ту, что минимизирует критерий
    задачи, а это видно только после полного расчёта каждой (см.
    ``_pick_best_candidate``).
    """
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
    """Ближайшая к ``point`` (UTM) площадка — именно ближайшая, не первая в слое."""
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
    """Площадка вылета и посадки конкретного экземпляра БВС.

    У разных бортов одного парка она может быть разной: собственная локация
    экземпляра, если она задана в парке; иначе ближайшая к области облёта ВПП
    обстановки; если ВПП в обстановке нет вовсе — центроид рабочей области.
    Отдельного шага «выбрать одну площадку на весь план» нет: борта летают
    оттуда, где физически стоят.
    """
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
    """Один физический этап вылета — переход или сам галс.

    Переход обходит запрещённые зоны через ``visibility.find_path``. Этапы
    нужны дважды: чтобы честно посчитать налёт (переходы входят в бюджет) и
    чтобы показать подробное расписание вылета (ИНТ.ФТ.15).
    """

    kind: str  # "transit" | "survey"
    label: str
    coords: list[tuple[float, float]]  # UTM, включая обе граничные точки
    length_m: float
    fallback: bool = False


def _build_sortie_legs(
    sortie: Sortie, start_point: Point, launch_name: str | None, restricted_zones: list[BaseGeometry]
) -> list[RouteLeg]:
    """Раскладывает вылет на этапы: взлёт и перелёт до зоны задания, каждый
    галс, переходы между галсами, возврат и посадка.

    Переходы обходят ``restricted_zones`` сеточным A*, если прямая их
    пересекает. Сами галсы не проверяются — они лежат в рабочей области,
    уже построенной без этих зон. Сумма ``length_m`` всех этапов и есть
    фактический налёт вылета.
    """
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
    """Полностью рассчитанный план для одной группы «модель+камера» — один из
    нескольких, между которыми выбирает ``_pick_best_candidate``."""

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
    """ПЛН.ФТ.2: побеждает кандидат, минимизирующий критерий задачи —
    нормированная взвешенная сумма ``J = α·J1/J1* + (1-α)·J2/J2*``.

    ``J1*``/``J2*`` — лучшие значения среди уже посчитанных кандидатов, а не
    теоретические оптимумы. При единственном кандидате сравнивать не с чем,
    деление не выполняется вовсе. ``criterion_alpha`` уже однозначно кодирует
    режим критерия (1.0 «Время», 0.0 «Налет», значение оператора для
    «Компромисс»), поэтому режим отдельно не нужен.
    """
    if len(candidates) == 1:
        return candidates[0]

    j1_best = min(c.j1_s for c in candidates)
    j2_best = min(c.j2_s for c in candidates)

    def score(c: _Candidate) -> float:
        j1_term = (c.j1_s / j1_best) if j1_best > 0 else 0.0
        j2_term = (c.j2_s / j2_best) if j2_best > 0 else 0.0
        return criterion_alpha * j1_term + (1 - criterion_alpha) * j2_term

    return min(candidates, key=score)


def create_plan(task_id: str, progress: ProgressReporter | None = None) -> PlanSummary:
    """Полный конвейер расчета (ПЛН.ФТ.5).

    ``progress`` — необязательный репортер фонового расчета: он публикует
    стадию и процент и на каждом тике поднимает ``JobCancelled``, если
    оператор нажал «Отменить». Синхронный вызов (существующие тесты, отладка)
    передает ``None``, и поведение функции не меняется ни на шаг.
    """
    progress = progress or ProgressReporter(None)
    progress.stage("load")
    task = task_service.get_task(task_id)
    env = environment_service.get_environment(task.environment_id)
    if env.status != "Корректна":
        raise PlanInfeasibleError("обстановка задачи содержит ошибки и недоступна для расчета")

    progress.stage("model")
    spectrum = _survey_type_spectrum(task.survey_type)
    # Кандидаты берутся только из парка задачи, а не из всех загруженных:
    # парк выбран оператором при постановке, совместимость с обстановкой уже
    # проверена в task_service.
    eligible = fleet_service.eligible_instances(task.fleet_id)
    if not eligible:
        raise PlanInfeasibleError(
            f"в парке «{task.fleet_name}» нет ни одного экземпляра БВС в статусе «Готов»"
        )

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

    # Площадка вылета — своя у каждого экземпляра, и от группы «модель+камера»
    # она не зависит: считается один раз на все кандидаты.
    launch_by_inv: dict[str, tuple[Point, str | None]] = {
        inst.inventory_number: _instance_launch_point(inst, launch_feats, area_centroid, projector)
        for instances in groups.values()
        for inst in instances
    }

    lat, lon = area_geom.centroid.y, area_geom.centroid.x
    window_start_hour = _time_to_hours(task.window_start) or 0.0
    window_end_hour = _time_to_hours(task.window_end) or 24.0

    def build_candidate(
        model_key: str, camera_key: str, instances: list, tick: CandidateProgress
    ) -> _Candidate:
        model = FLEET_MODELS[model_key]
        tick.stage("Геометрия съемки", 0.0)
        try:
            survey_geometry = plan_survey_geometry(model_key, camera_key, task.gsd_cm)
        except CameraError as exc:
            raise PlanInfeasibleError(str(exc)) from exc

        # Зоны для обхода на переходах — те же контуры, что compute_working_area
        # вычитает из рабочей области. Высота своя у каждого кандидата (зависит
        # от камеры), поэтому ни restricted_zones, ни working_area между
        # кандидатами переиспользовать нельзя.
        restricted_zones: list[BaseGeometry] = [zone.footprint() for zone in no_fly_zones] + [
            obstacle.footprint() for obstacle in obstacles if obstacle.is_hole_at(survey_geometry.height_m)
        ]

        tick.stage("Построение рабочей области", 0.10)
        try:
            working_area = compute_working_area(
                area_utm, allowed_zones, no_fly_zones, obstacles, survey_geometry.height_m
            )
        except GeometryError as exc:
            raise PlanInfeasibleError(str(exc)) from exc
        if working_area.is_empty:
            raise PlanInfeasibleError("рабочая область пуста на высоте съемки — нет свободного места для галсов")

        tick.stage("Декомпозиция области", 0.20)
        cells = boustrophedon_cells(working_area)
        max_route_m = model.max_route_km * 1000.0 if model.max_route_km else float("inf")

        raw_tracks: list[LineString] = []
        for cell_index, cell in enumerate(cells):
            tick.span("Построение галсов", cell_index, len(cells), (0.25, 0.50))
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
        launch_name_by_vehicle = {
            inst.inventory_number: launch_by_inv[inst.inventory_number][1] for inst in instances
        }
        track_objs = [Track(id=f"track-{i}", geometry=t) for i, t in enumerate(raw_tracks)]

        tick.stage("Распределение по БВС", 0.50)
        # Кластеризация по площадкам + TSP-тур + балансировка уже учитывают
        # переходы при разбиении на вылеты, но оценивают их по прямой.
        # Фактический маршрут в обход зон строится ниже, и налёт вылета
        # пересчитывается по нему ДО расписания: световой день обязан
        # укладывать настоящую длительность, а не приблизительную.
        routing_result = cluster_assign_and_route(track_objs, vehicles)

        sortie_legs: dict[int, list[RouteLeg]] = {}
        any_transit_fallback = False
        for vehicle_index, vehicle in enumerate(vehicles):
            tick.span("Обход зон на переходах", vehicle_index, len(vehicles), (0.55, 0.80))
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

        for vehicle_index, vehicle in enumerate(vehicles):
            tick.span("Расписание вылетов", vehicle_index, len(vehicles), (0.80, 0.98))
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
                # Курсор передаётся от этапа к этапу, а не пересчитывается из
                # накопленной суммы секунд, — иначе соседние этапы расходятся
                # на микросекунду.
                cursor = sched.start_utc
                for leg in legs:
                    route_coords.extend(leg.coords if not route_coords else leg.coords[1:])
                    phase_end = cursor + timedelta(seconds=leg.length_m / cruise_speed)
                    phases.append(PlanSortiePhase(
                        label=leg.label, kind=leg.kind, start_utc=cursor, end_utc=phase_end,
                        distance_m=leg.length_m,
                    ))
                    cursor = phase_end
                if phases:
                    # sched.end_utc посчитан отдельно, из суммы длин за один
                    # раз, — синхронизируем последний этап явно, чтобы не
                    # осталось расхождения от разного порядка операций с float.
                    phases[-1] = phases[-1].model_copy(update={"end_utc": sched.end_utc})

                route_line_utm = LineString(route_coords)
                route_line_wgs84 = projector.to_wgs84(route_line_utm)
                survey_tracks_wgs84 = projector.to_wgs84(
                    MultiLineString([t.geometry for t in sched.sortie.tracks])
                )
                # Длина всего маршрута (переходы в обход зон + галсы), а не
                # только галсов: так честнее относительно карты.
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
    for index, ((model_key, camera_key), instances) in enumerate(groups.items()):
        tick = CandidateProgress(progress, FLEET_MODELS[model_key].name, index, len(groups))
        try:
            candidates.append(build_candidate(model_key, camera_key, instances, tick))
        except PlanInfeasibleError as exc:
            failures.append(f"{FLEET_MODELS[model_key].name}: {exc}")

    if not candidates:
        raise PlanInfeasibleError(
            "ни одна из подходящих по нагрузке моделей БВС не позволяет рассчитать план: "
            + "; ".join(failures)
        )

    best = _pick_best_candidate(candidates, task.criterion_alpha)

    progress.stage("save")
    plan_id = str(uuid.uuid4())
    version = repositories.plans.next_version(task.id)
    detail = PlanDetail(
        id=plan_id,
        task_id=task.id,
        version=version,
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
    repositories.plans.add(detail)
    task_service.mark_calculated(task.id)
    with log_context(task_id=task.id, plan_id=plan_id):
        log.info(
            "план рассчитан",
            extra={
                "version": version, "uav_model": best.model_key,
                "candidates": len(candidates), "rejected": len(failures),
                "sortie_count": len(best.plan_sorties),
                "j1_s": round(best.j1_s, 1), "j2_s": round(best.j2_s, 1),
                "warnings": len(best.warnings),
            },
        )
    return _to_summary(detail)


def list_plans(task_id: str) -> list[PlanSummary]:
    return [_to_summary(p) for p in repositories.plans.list_by_task_newest_first(task_id)]


def get_plan(plan_id: str) -> PlanDetail:
    return repositories.plans.get(plan_id)


def get_plan_summary(plan_id: str) -> PlanSummary:
    """Карточка плана без вылетов — то, что отдает ответ на запуск расчета."""
    return _to_summary(repositories.plans.get(plan_id))


def confirm_plan(plan_id: str, user: str) -> PlanSummary:
    """ЭКС.ФТ.6: ручное подтверждение плана оператором.

    Два условия, и они разной природы. Предусловие ЭКС.ФТ.2 — «последняя
    проверка безопасности выполнена и не содержит нарушений» — проверяется
    чтением отчета: это правило предметной области, и его нарушение означает,
    что оператору вообще не следовало показывать кнопку. Само же изменение
    статуса делается условным ``UPDATE ... WHERE status = 'Проверен'`` в
    репозитории: это защита от гонки (ЭКС.ФТ.9), а не от неверного состояния,
    и читать-проверять-писать здесь нельзя в принципе.
    """
    plan = repositories.plans.get(plan_id)
    report = repositories.safety.latest_report(plan_id)
    if report is None:
        raise PlanNotConfirmableError(
            "план не подтверждается: проверка безопасности еще не выполнялась"
        )
    if report.plan_id != plan.id:
        # Отчет относится к другой версии (после автопересчета БЕЗ.ФТ.3
        # запрошенная и проверенная версии расходятся).
        raise PlanNotConfirmableError(
            "план не подтверждается: последняя проверка относится к другой версии плана "
            f"(версия {plan.version} не проверялась)"
        )
    if report.status != "Пройдена":
        raise PlanNotConfirmableError(
            "план не подтверждается: последняя проверка безопасности содержит нарушения"
        )

    confirmed = repositories.plans.confirm(plan_id, user)
    if confirmed is None:
        actual = repositories.plans.get(plan_id)
        log.warning(
            "подтверждение отклонено — статус плана изменился",
            extra={"plan_id": plan_id, "plan_status": actual.status, "user": user},
        )
        raise PlanConfirmConflictError(actual)
    log.info(
        "статус плана изменен",
        extra={"plan_id": plan_id, "plan_status": confirmed.status, "user": user},
    )
    return _to_summary(confirmed)
