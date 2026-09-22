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
from datetime import datetime, time, timezone
from typing import Any

from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.domain.errors import ConflictError, ValidationError
from uav_planner.domain.errors import PlanInfeasibleError as _DomainPlanInfeasibleError
from uav_planner.camera import CAMERA_SPECS, CameraError, plan_survey_geometry
from uav_planner.coverage import boustrophedon_cells, generate_tracks, split_long_track
from uav_planner.fleet import FLEET_MODELS, flight_time_budget_s
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
from uav_planner.jobs.progress import SCHEDULE_SPAN, TRACKS_SPAN, ProgressReporter
from uav_planner.logging_setup import log_context
from uav_planner.routing.greedy import Track, Vehicle, greedy_assign_and_split
from uav_planner.schedule import ScheduleError, assign_timestamps

from . import fleet_service
from . import environment_service
from . import task_service
from uav_planner.api.schemas.plan import PlanDetail, PlanSortie, PlanSummary


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


def _pick_model_group(survey_type: str, eligible: list | None = None) -> tuple[str, str, list]:
    """ПЛН.ФТ.10-подготовка: выбор модели БВС и совместимой камеры — группа
    готовых экземпляров с подходящей нагрузкой, в которой больше всего экземпляров.

    Состав парка стал явным аргументом вместо скрытого глобального чтения
    внутри функции: фоновая работа должна брать его один раз и не зависеть от
    того, что оператор перезалил парк посреди получасового счета.
    """
    spectrum = SPECTRUM_BY_SURVEY_TYPE.get(survey_type)
    if spectrum is None:
        raise PlanInfeasibleError(
            f"тип съемки «{survey_type}» пока не поддерживается расчетным ядром "
            "(LiDAR и геофизическая съемка — модельные профили, будущая версия)"
        )

    if eligible is None:
        eligible = fleet_service.eligible_instances()
    if not eligible:
        raise PlanInfeasibleError("нет загруженного парка БВС в статусе «Готов»")

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

    if not groups:
        raise PlanInfeasibleError(
            f"нет готовых БВС с нагрузкой, совместимой с типом съемки «{survey_type}»"
        )

    (model_key, camera_key), instances = max(groups.items(), key=lambda kv: len(kv[1]))
    return model_key, camera_key, instances


def _sortie_route_coords(sortie, start_point: Point) -> list[tuple[float, float]]:
    """Собирает маршрут вылета: от площадки через галсы (каждый — в том
    направлении, что ближе к текущему концу пути) и обратно на площадку.
    Переходы — прямые линии (без учета препятствий, см. модуль ``visibility``)."""
    coords: list[tuple[float, float]] = [(start_point.x, start_point.y)]
    current = start_point
    for track in sortie.tracks:
        line_coords = list(track.geometry.coords)
        start_pt, end_pt = line_coords[0], line_coords[-1]
        if current.distance(Point(end_pt)) < current.distance(Point(start_pt)):
            line_coords = line_coords[::-1]
        coords.extend(line_coords)
        current = Point(line_coords[-1])
    coords.append((start_point.x, start_point.y))
    return coords


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
    model_key, camera_key, instances = _pick_model_group(
        task.survey_type, fleet_service.eligible_instances()
    )
    model = FLEET_MODELS[model_key]

    progress.stage("survey_geometry")
    try:
        survey_geometry = plan_survey_geometry(model_key, camera_key, task.gsd_cm)
    except CameraError as exc:
        raise PlanInfeasibleError(str(exc)) from exc

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

    progress.stage("working_area")
    validate_polygon(area_geom)
    area_utm = projector.to_utm(area_geom)
    try:
        working_area = compute_working_area(
            area_utm, allowed_zones, no_fly_zones, obstacles, survey_geometry.height_m
        )
    except GeometryError as exc:
        raise PlanInfeasibleError(str(exc)) from exc

    if working_area.is_empty:
        raise PlanInfeasibleError("рабочая область пуста на высоте съемки — нет свободного места для галсов")

    progress.stage("decomposition")
    cells = boustrophedon_cells(working_area)
    max_route_m = model.max_route_km * 1000.0 if model.max_route_km else float("inf")

    # Первый из двух циклов, съедающих время на большой сцене, — отсюда и
    # берется гранулярность прогресса, без правок внутри coverage.
    raw_tracks: list[LineString] = []
    for cell_index, cell in enumerate(cells):
        progress.span("tracks", cell_index, len(cells), TRACKS_SPAN)
        for track in generate_tracks(cell, survey_geometry.track_spacing_m):
            raw_tracks.extend(split_long_track(track, max_route_m))

    if not raw_tracks:
        raise PlanInfeasibleError("рабочая область слишком мала для построения ни одного галса")

    if launch_feats:
        launch_point_utm = projector.to_utm(shape(launch_feats[0]["geometry"]))
        launch_name = launch_feats[0]["properties"].get("name") or "ВПП"
    else:
        launch_point_utm = working_area.centroid
        launch_name = None

    wind_speed = task.wind_speed_ms or 0.0
    cruise_speed = max(model.speed_ms.max_ms - wind_speed, MIN_EFFECTIVE_SPEED_MPS)
    budget_s = flight_time_budget_s(model)

    progress.stage("assignment")
    vehicles = [Vehicle(id=inst.inventory_number, speed_mps=cruise_speed, budget_s=budget_s) for inst in instances]
    track_objs = [Track(id=f"track-{i}", geometry=t) for i, t in enumerate(raw_tracks)]
    routing_result = greedy_assign_and_split(track_objs, vehicles)

    lat, lon = area_geom.centroid.y, area_geom.centroid.x
    window_start_hour = _time_to_hours(task.window_start) or 0.0
    window_end_hour = _time_to_hours(task.window_end) or 24.0

    plan_sorties: list[PlanSortie] = []
    total_flight_s = 0.0
    plan_start: datetime | None = None
    plan_end: datetime | None = None

    # Второй длинный цикл — по бортам; здесь же живет проверка отмены.
    for vehicle_index, vehicle in enumerate(vehicles):
        progress.span("schedule", vehicle_index, len(vehicles), SCHEDULE_SPAN)
        sorties = routing_result.sorties_by_vehicle.get(vehicle.id, [])
        try:
            scheduled = assign_timestamps(
                sorties, lat, lon, task.work_date,
                window_start_hour=window_start_hour, window_end_hour=window_end_hour,
            )
        except ScheduleError as exc:
            raise PlanInfeasibleError(str(exc)) from exc

        for idx, sched in enumerate(scheduled):
            route_coords = _sortie_route_coords(sched.sortie, launch_point_utm)
            route_line_wgs84 = projector.to_wgs84(LineString(route_coords))
            survey_tracks_wgs84 = projector.to_wgs84(
                MultiLineString([t.geometry for t in sched.sortie.tracks])
            )
            distance_m = sum(t.length_m for t in sched.sortie.tracks)

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
        j1_s=j1_s,
        j2_s=j2_s,
        is_optimal=False,
        uav_model=model.name,
        sortie_count=len(plan_sorties),
        warnings=warnings,
        model_key=model_key,
        camera_key=camera_key,
        height_m=survey_geometry.height_m,
        swath_m=survey_geometry.swath_m,
        cruise_speed_mps=cruise_speed,
        budget_s=budget_s,
        sorties=plan_sorties,
    )
    repositories.plans.add(detail)
    task_service.mark_calculated(task.id)
    with log_context(task_id=task.id, plan_id=plan_id):
        log.info(
            "план рассчитан",
            extra={
                "version": version, "uav_model": model_key, "sortie_count": len(plan_sorties),
                "j1_s": round(j1_s, 1), "j2_s": round(j2_s, 1), "warnings": len(warnings),
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
