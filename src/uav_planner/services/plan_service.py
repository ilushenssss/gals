"""Оркестрация расчета плана — первая версия модуля «Планирование».

Пайплайн: Environment + Task + Fleet -> рабочая область (``geometry``) ->
для каждой группы «модель + камера» (кандидата): высота съемки и шаг галсов
(``camera``) -> декомпозиция и галсы (``coverage``) -> кластеризация по
площадкам, тур и балансировка (``routing``) -> обход зон на переходах
(``visibility``) и облет рельефа (``terrain``) -> расписание (``schedule``,
световой день и окно работ в поясе задачи) -> метрики J1/J2 -> выбор лучшего
кандидата по критерию задачи -> ``Plan``.

См. docs/trebovania/Планирование.md (ПЛН.ФТ.1-10) и docs/trebovania/
Математическая_модель.md; расхождения с ТЗ — docs/AUDIT.md. Известные
упрощения первой версии:
  - одна модель БВС на план — выбирается лучший из кандидатов «модель+камера»
    (смешанный парк в одной задаче — будущая версия);
  - крейсерская скорость = паспортный максимум модели минус скорость ветра
    задачи (без учета направления — консервативная оценка); ветер выше
    допустимого для модели отбрасывает кандидата;
  - развороты между галсами не моделируются (прямые переходы или обход A*);
  - расчет не сохраняет промежуточное допустимое решение, поэтому остановка по
    лимиту времени (ПЛН.ФТ.3) фиксируется статусом, но отдать «лучшее из
    найденного» нечего.

Расчет выполняется фоновой работой (``jobs/tasks.py``), а независимая проверка
результата — отдельный модуль «Проверка безопасности».
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from uav_planner.routing import Sortie, Track, Vehicle, cluster_assign_and_route, split_into_sorties
from uav_planner.schedule import DEFAULT_LAUNCH_INTERVAL_S, ScheduleError, assign_timestamps, work_window_utc
from uav_planner.terrain import (
    ConstantElevationProvider,
    ElevationLookupError,
    ElevationProvider,
    plan_altitude_profile,
)
from uav_planner.safety import SortieTrack, check_separation
from uav_planner.visibility import find_path
from uav_planner.config import get_settings

from . import fleet_service
from . import environment_service
from . import task_service
from . import terrain_service
from uav_planner.api.schemas.plan import PlanDetail, PlanSortie, PlanSortiePhase, PlanSummary


SPECTRUM_BY_SURVEY_TYPE = {
    "RGB": "rgb",
    "мультиспектральная": "multispectral",
    "ИК": "thermal",
}

SORTIE_PENALTY_S = 60.0  # λ в J2 — вес одного вылета (износ на взлете/посадке)
MIN_EFFECTIVE_SPEED_MPS = 1.0
# Сколько раз можно сдвинуть взлет борта на интервал выпуска, разводя его по
# времени с уже расписанными бортами (60 × 60 с — до часа задержки).
MAX_DECONFLICT_DELAYS = 60
# Сколько раз за расчет кандидата можно перерезать вылет, который после
# обхода зон (A*) вышел за энергобюджет — защита от зацикливания.
MAX_BUDGET_RESPLITS = 50

_SUMMARY_ONLY_EXCLUDE = {"sorties"}

log = logging.getLogger(__name__)
# Внутри build_candidate имя ``log`` занято историей расчета (список строк),
# поэтому журнал там — через это имя. Раньше там звался ``log.warning`` на
# списке, и недоступный рельеф ронял весь расчет AttributeError'ом вместо
# честной деградации на плоскую высоту.
_logger = log


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
    # Средняя высота этапа над рельефом (AGL) — заполняется отдельным шагом
    # облета рельефа (``_apply_terrain_profile``), None до него/если рельеф
    # выключен или недоступен. ``coords`` тогда остаются 2D (UTM x, y); после
    # облета рельефа в них добавляется третья координата — абсолютная Z.
    height_agl_m: float | None = None


def _build_sortie_legs(
    sortie: Sortie,
    start_point: Point,
    launch_name: str | None,
    restricted_zones: list[BaseGeometry],
    allowed_space: BaseGeometry | None,
) -> list[RouteLeg]:
    """Раскладывает вылет на этапы: взлёт и перелёт до зоны задания, каждый
    галс, переходы между галсами, возврат и посадка.

    Переходы обходят ``restricted_zones`` (БПЗ/препятствия) и не выходят за
    границу ``allowed_space`` сеточным A*, если прямая нарушает то или
    другое — раньше проверялись только явные препятствия, и переход по
    прямой мог срезать «залив» невыпуклой границы разрешенного пространства
    никого не задев (см. ``visibility.astar.find_path``). Сами галсы не
    проверяются — они лежат в рабочей области, уже построенной внутри этой
    границы. Сумма ``length_m`` всех этапов и есть фактический налёт вылета.
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

        transit = find_path(current, next_point, restricted_zones, allowed_space)
        if transit.line.length > _MIN_LEG_LENGTH_M:
            label = takeoff_label if idx == 0 else f"Переход к галсу {idx + 1}"
            legs.append(RouteLeg("transit", label, list(transit.line.coords), transit.line.length, transit.fallback))

        legs.append(RouteLeg("survey", f"Галс {idx + 1}", line_coords, LineString(line_coords).length))
        current = Point(line_coords[-1])

    transit = find_path(current, start_point, restricted_zones, allowed_space)
    if transit.line.length > _MIN_LEG_LENGTH_M:
        legs.append(RouteLeg("transit", landing_label, list(transit.line.coords), transit.line.length, transit.fallback))

    return legs


def _route_line(legs: list[RouteLeg]) -> LineString:
    """Маршрут вылета одной ломаной (UTM, 2D) — из этапов, как и в карточке
    плана."""
    coords: list[tuple[float, float]] = []
    for leg in legs:
        leg_2d = [(c[0], c[1]) for c in leg.coords]
        coords.extend(leg_2d if not coords else leg_2d[1:])
    return LineString(coords)


def _conflicts(new_tracks: list[SortieTrack], placed: list[SortieTrack], min_separation_m: float) -> bool:
    """Сближается ли хоть один вылет ``new_tracks`` с уже расписанными
    вылетами других бортов ближе ``min_separation_m`` (тот же точный расчет
    наибольшего сближения, что у проверки безопасности)."""
    return any(
        not check_separation([new, other], min_separation_m).passed
        for new in new_tracks
        for other in placed
    )


def _apply_terrain_profile(
    legs: list[RouteLeg],
    start_point: Point,
    projector: Projector,
    target_agl_m: float,
    climb_angle_deg: float,
    provider: ElevationProvider,
) -> list[RouteLeg]:
    """Встраивает высоту над рельефом в уже построенные этапы вылета.

    Высоты запрашиваются только в естественных точках маршрута — границах
    этапов (см. ``uav_planner.terrain``), а не на каждой точке A*-обхода
    зоны. Внутри этапа Z интерполируется линейно по пройденному расстоянию
    между его двумя концами — этого достаточно: рельеф не меняется резко на
    длине одного перехода/галса, а угол набора уже ограничил сам целевой
    профиль на границах.
    """
    keypoints_utm = [start_point] + [Point(leg.coords[-1][0], leg.coords[-1][1]) for leg in legs]
    keypoints_wgs84 = [(p.x, p.y) for p in (projector.to_wgs84(kp) for kp in keypoints_utm)]
    distances_m = [leg.length_m for leg in legs]

    z_at_keypoints = plan_altitude_profile(keypoints_wgs84, distances_m, target_agl_m, climb_angle_deg, provider)
    ground_at_keypoints = provider.elevations(keypoints_wgs84)

    new_legs: list[RouteLeg] = []
    for i, leg in enumerate(legs):
        z_start, z_end = z_at_keypoints[i], z_at_keypoints[i + 1]
        coords_2d = [(x, y) for x, y, *_ in leg.coords]
        cumulative = [0.0]
        for a, b in zip(coords_2d, coords_2d[1:]):
            cumulative.append(cumulative[-1] + Point(a).distance(Point(b)))
        total = cumulative[-1] or 1.0
        coords_3d = [
            (x, y, z_start + (z_end - z_start) * (c / total)) for (x, y), c in zip(coords_2d, cumulative)
        ]
        agl_m = ((z_start + z_end) / 2.0) - ((ground_at_keypoints[i] + ground_at_keypoints[i + 1]) / 2.0)
        new_legs.append(
            RouteLeg(leg.kind, leg.label, coords_3d, leg.length_m, leg.fallback, height_agl_m=agl_m)
        )
    return new_legs


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
    log: list[str]


def _fmt_hm(seconds: float) -> str:
    """Секунды -> «Nч Mмин» для истории расчета — короче и понятнее оператору,
    чем сырые секунды."""
    total_min = round(seconds / 60.0)
    hours, minutes = divmod(int(total_min), 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def _candidate_label(c: _Candidate) -> str:
    return f"{c.model.name} + камера {CAMERA_SPECS[c.camera_key].name}"


def _pick_best_candidate(candidates: list[_Candidate], criterion_alpha: float, criterion_mode: str) -> _Candidate:
    """ПЛН.ФТ.2: побеждает кандидат, минимизирующий критерий задачи —
    нормированная взвешенная сумма ``J = α·J1/J1* + (1-α)·J2/J2*``.

    ``J1*``/``J2*`` — лучшие значения среди уже посчитанных кандидатов, а не
    теоретические оптимумы. При единственном кандидате сравнивать не с чем,
    деление не выполняется вовсе. ``criterion_alpha`` уже однозначно кодирует
    режим критерия (1.0 «Время», 0.0 «Налет», значение оператора для
    «Компромисс»), поэтому режим отдельно не нужен для самого расчета —
    только для фразы истории расчета (``criterion_mode``).

    Дописывает в лог выигравшего кандидата финальную фразу — сравнение (или
    «единственный кандидат», если сравнивать не с чем): история расчета
    должна объяснять именно ВЫБОР, не только числа по кандидатам.
    """
    if len(candidates) == 1:
        winner = candidates[0]
        winner.log.append(
            f"Единственный подходящий кандидат — сравнение не требуется, выбран «{_candidate_label(winner)}»."
        )
        return winner

    j1_best = min(c.j1_s for c in candidates)
    j2_best = min(c.j2_s for c in candidates)

    def score(c: _Candidate) -> float:
        j1_term = (c.j1_s / j1_best) if j1_best > 0 else 0.0
        j2_term = (c.j2_s / j2_best) if j2_best > 0 else 0.0
        return criterion_alpha * j1_term + (1 - criterion_alpha) * j2_term

    winner = min(candidates, key=score)
    winner.log.append(
        f"Сравнены {len(candidates)} кандидата(ов) по критерию «{criterion_mode}» "
        f"(J = α·J1/J1* + (1−α)·J2/J2*, α = {criterion_alpha:.2f}); выбран «{_candidate_label(winner)}» — "
        f"у него наименьшее значение J ({score(winner):.3f}) среди посчитанных."
    )
    return winner


def create_plan(
    task_id: str,
    progress: ProgressReporter | None = None,
    *,
    extra_no_fly_buffer_m: float = 0.0,
) -> PlanSummary:
    """Полный конвейер расчета (ПЛН.ФТ.5).

    ``progress`` — необязательный репортер фонового расчета: он публикует
    стадию и процент и на каждом тике поднимает ``JobCancelled``, если
    оператор нажал «Отменить». Синхронный вызов (существующие тесты, отладка)
    передает ``None``, и поведение функции не меняется ни на шаг.

    ``extra_no_fly_buffer_m`` — скорректированный запрос автопересчета
    (БЕЗ.ФТ.3, «геозона → перестроить перелет»): запас, добавляемый к буферу
    каждой БПЗ и каждого препятствия из обстановки. Рабочая область, галсы и
    обход A* на переходах строятся уже от расширенных контуров, поэтому
    маршрут отходит от запретной зоны дальше, чем требует сама обстановка.
    Проверка безопасности по-прежнему сверяет план с исходными зонами — запас
    остается запасом, а не новым правилом.
    """
    if extra_no_fly_buffer_m < 0:
        raise ValueError("extra_no_fly_buffer_m не может быть отрицательным")
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

    # История расчета (простым языком, см. docs/trebovania/ и запрос
    # оператора) — начинается здесь, до самой геометрии, и продолжается
    # внутри build_candidate по мере реальных вычислений (не отдельным
    # пересчетом «для текста», а той же переменной, что уже участвует в
    # расчете).
    intro_log = [
        f"Задача «{task.name}»: обстановка «{task.environment_name}», парк «{task.fleet_name}», "
        f"тип съемки {task.survey_type}, GSD {task.gsd_cm:g} см.",
        f"Подходящих по камере групп «модель+камера»: {len(groups)} ("
        + ", ".join(f"{FLEET_MODELS[mk].name} + камера {CAMERA_SPECS[ck].name}" for mk, ck in groups) + ").",
    ]

    adjustment_note: str | None = None
    if extra_no_fly_buffer_m > 0:
        adjustment_note = (
            "по результатам проверки безопасности (пересечение запретных зон) буфер вокруг каждой БПЗ "
            f"и препятствия увеличен на {extra_no_fly_buffer_m:g} м сверх заданного в обстановке — "
            "рабочая область и переходы перестроены"
        )
        intro_log.append(adjustment_note[0].upper() + adjustment_note[1:] + ".")

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
            active_windows=environment_service.parse_time_windows(f["properties"].get("active_windows")),
        )
        for i, f in enumerate(airspace_feats)
    ]
    # Интервалы действия БПЗ при планировании не используются — БПЗ
    # обходится всегда (консервативно, момент вылета еще неизвестен). Точное
    # сравнение с временем каждого вылета делает проверка безопасности.
    no_fly_zones = [
        NoFlyZone(
            id=str(i),
            polygon=projector.to_utm(shape(f["geometry"])),
            safety_buffer_m=float(f["properties"].get("safety_buffer_m", 0.0)) + extra_no_fly_buffer_m,
            active_windows=environment_service.parse_time_windows(f["properties"].get("active_windows")),
        )
        for i, f in enumerate(no_fly_feats)
    ]
    obstacles = [
        Obstacle(
            id=str(i),
            polygon=projector.to_utm(shape(f["geometry"])),
            height=HeightRange(float(f["properties"]["h_min"]), float(f["properties"]["h_max"])),
            # Раньше буфер препятствия из обстановки сюда не передавался (у
            # БПЗ — передавался): галсы подходили к препятствию вплотную.
            safety_buffer_m=float(f["properties"].get("safety_buffer_m", 0.0)) + extra_no_fly_buffer_m,
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
    tz = task_service.task_tzinfo(task)
    tz_label = task.timezone or "UTC"

    # Разрешенная зона с интервалами действия годится для плана, только если
    # действует все окно работ даты задачи (консервативно: какой вылет когда
    # взлетит, станет известно лишь после расписания). Если на дату рабочих
    # часов нет вовсе, фильтровать не по чему — расписание само перенесет
    # вылеты, а проверка безопасности сверит зоны с их фактическим временем.
    effective_window = work_window_utc(lat, lon, task.work_date, task.window_start, task.window_end, tz)
    planning_allowed_zones = allowed_zones
    if effective_window is not None:
        planning_allowed_zones = [z for z in allowed_zones if z.is_active_throughout(*effective_window)]
        if allowed_zones and not planning_allowed_zones:
            raise PlanInfeasibleError(
                "ни одна зона разрешенного воздушного пространства не действует в течение всего окна работ "
                f"{task.work_date.isoformat()} — измените дату/окно работ или интервалы действия зон"
            )

    # Один провайдер на весь расчет (все кандидаты) — его кэш по координатам
    # тогда работает и между кандидатами, не только внутри одного. Профиль
    # высоты строится всегда (иначе колонка геометрии в БД была бы то 2D, то
    # 3D) — если рельеф выключен/недоступен, честный фолбэк на постоянную
    # высоту (Z = целевая высота съемки, как было до этой функции), а не
    # смешение размерностей.
    real_elevation_provider = terrain_service.default_elevation_provider()
    settings = get_settings()

    def build_candidate(
        model_key: str, camera_key: str, instances: list, tick: CandidateProgress
    ) -> _Candidate:
        model = FLEET_MODELS[model_key]
        camera = CAMERA_SPECS[camera_key]
        log: list[str] = []
        tick.stage("Геометрия съемки", 0.0)
        wind_speed = task.wind_speed_ms or 0.0
        if wind_speed > model.max_wind_ms:
            # ПЛН.ФТ.10: превышение ветрового ограничения модели — причина
            # невыполнимости, а не повод молча считать с урезанной скоростью.
            raise PlanInfeasibleError(
                f"ветер задачи {wind_speed:g} м/с превышает допустимый для модели «{model.name}» "
                f"({model.max_wind_ms:g} м/с)"
            )
        try:
            survey_geometry = plan_survey_geometry(model_key, camera_key, task.gsd_cm)
        except CameraError as exc:
            raise PlanInfeasibleError(str(exc)) from exc

        gsd_m = task.gsd_cm / 100.0
        log.append(
            f"Высота съемки по GSD (камера «{camera.name}»): "
            f"H = GSD·f·N_w/s_w = {gsd_m:.3f}·{camera.focal_length_mm:.1f}·{camera.frame_width_px}/"
            f"{camera.sensor_width_mm:.1f} = {survey_geometry.height_m:.1f} м."
        )
        log.append(
            "Полоса захвата и шаг между галсами: "
            f"B = H·s_w/f = {survey_geometry.height_m:.1f}·{camera.sensor_width_mm:.1f}/{camera.focal_length_mm:.1f} "
            f"= {survey_geometry.swath_m:.1f} м; d = B·(1 − q_попер) = {survey_geometry.swath_m:.1f}·(1 − 0.7) "
            f"= {survey_geometry.track_spacing_m:.1f} м."
        )

        # Зоны для обхода на переходах — те же контуры, что compute_working_area
        # вычитает из рабочей области. Высота своя у каждого кандидата (зависит
        # от камеры), поэтому ни restricted_zones, ни working_area между
        # кандидатами переиспользовать нельзя.
        restricted_zones: list[BaseGeometry] = [zone.footprint() for zone in no_fly_zones] + [
            obstacle.footprint() for obstacle in obstacles if obstacle.is_hole_at(survey_geometry.height_m)
        ]
        # Тот же фильтр по высоте/активности, что compute_working_area
        # использует при пересечении area∩allowed — передаём A* на переходах,
        # чтобы он не выпускал маршрут за границу разрешенного пространства
        # (раньше проверялись только явные препятствия, см. _build_sortie_legs).
        active_allowed = [
            zone.polygon for zone in planning_allowed_zones
            if zone.height.contains(survey_geometry.height_m) and zone.is_active_at()
        ]
        allowed_union = unary_union(active_allowed) if active_allowed else None

        tick.stage("Построение рабочей области", 0.10)
        try:
            working_area = compute_working_area(
                area_utm, planning_allowed_zones, no_fly_zones, obstacles, survey_geometry.height_m
            )
        except GeometryError as exc:
            raise PlanInfeasibleError(str(exc)) from exc
        if working_area.is_empty:
            raise PlanInfeasibleError("рабочая область пуста на высоте съемки — нет свободного места для галсов")
        log.append(
            "Рабочая область (пересечение области облета и разрешенного пространства, минус бесполетные "
            f"зоны и препятствия выше {survey_geometry.height_m:.1f} м): площадь {working_area.area / 1e6:.2f} км²."
        )

        tick.stage("Декомпозиция области", 0.20)
        cells = boustrophedon_cells(working_area)
        max_route_m = model.max_route_km * 1000.0 if model.max_route_km else float("inf")
        log.append(f"Область разбита на {len(cells)} ячейк(и) без внутренних дыр.")

        raw_tracks: list[LineString] = []
        for cell_index, cell in enumerate(cells):
            tick.span("Построение галсов", cell_index, len(cells), (0.25, 0.50))
            for track in generate_tracks(cell, survey_geometry.track_spacing_m):
                raw_tracks.extend(split_long_track(track, max_route_m))
        if not raw_tracks:
            raise PlanInfeasibleError("рабочая область слишком мала для построения ни одного галса")
        log.append(
            f"В ячейках построено {len(raw_tracks)} галс(ов) общей длиной "
            f"{sum(t.length for t in raw_tracks) / 1000.0:.1f} км с шагом {survey_geometry.track_spacing_m:.1f} м."
        )

        cruise_speed = max(model.speed_ms.max_ms - wind_speed, MIN_EFFECTIVE_SPEED_MPS)
        budget_s = flight_time_budget_s(model, settings.energy_reserve, settings.maneuver_margin)
        log.append(
            "Энергобюджет вылета: T_бюдж = T_max·(1−η)·(1−m_ман) = "
            f"{model.max_flight_time_min * 60:.0f}·(1−{settings.energy_reserve:.2f})·(1−{settings.maneuver_margin:.2f}) "
            f"= {budget_s:.0f} с ({budget_s / 60.0:.1f} мин)."
        )

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
        n_sorties_raw = sum(len(sorties) for sorties in routing_result.sorties_by_vehicle.values())
        log.append(
            f"Галсы распределены между {len(vehicles)} БВС парка (модель «{model.name}», камера «{camera.name}»), "
            "переходы между ними и до площадок обойдены вокруг запретных зон и препятствий (сеточный A*); "
            f"маршруты разбиты на {n_sorties_raw} вылет(ов) по энергобюджету."
        )
        if routing_result.unassigned_tracks:
            log.append(
                f"{len(routing_result.unassigned_tracks)} галс(ов) не поместились в бюджет вылета ни одного "
                "борта — остались нераспределёнными."
            )

        sortie_legs: dict[int, list[RouteLeg]] = {}
        any_transit_fallback = False
        terrain_unavailable_reason: str | None = None
        terrain_applied = False
        active_provider: ElevationProvider = (
            real_elevation_provider if real_elevation_provider is not None else ConstantElevationProvider(0.0)
        )
        resplit_count = 0
        for vehicle_index, vehicle in enumerate(vehicles):
            tick.span("Обход зон на переходах", vehicle_index, len(vehicles), (0.55, 0.80))
            launch_name = launch_name_by_vehicle[vehicle.id]
            pending = list(routing_result.sorties_by_vehicle.get(vehicle.id, []))
            final_sorties: list[Sortie] = []
            while pending:
                sortie = pending.pop(0)
                legs = _build_sortie_legs(sortie, vehicle.launch_point, launch_name, restricted_zones, allowed_union)
                actual_s = sum(leg.length_m for leg in legs) / cruise_speed
                if actual_s > budget_s and len(sortie.tracks) > 1 and resplit_count < MAX_BUDGET_RESPLITS:
                    # Маршрутизация оценивала переходы по прямой, а обход зон
                    # удлинил их, и вылет перестал укладываться в бюджет. Его
                    # галсы перерезаются заново с бюджетом, уменьшенным в
                    # той же пропорции (прямая/факт), — и новые вылеты снова
                    # проходят обход зон. Одиночный галс резать некуда: такой
                    # вылет остается как есть, и его честно отметит проверка
                    # безопасности («Энергия»).
                    shrunk = Vehicle(
                        id=vehicle.id, speed_mps=vehicle.speed_mps,
                        budget_s=budget_s * sortie.flight_time_s / actual_s,
                        launch_point=vehicle.launch_point,
                    )
                    parts, leftover = split_into_sorties(sortie.tracks, shrunk)
                    if len(parts) > 1 and not leftover:
                        resplit_count += 1
                        pending[0:0] = parts
                        continue
                sortie.flight_time_s = actual_s
                if any(leg.fallback for leg in legs):
                    any_transit_fallback = True

                # Высотный профиль строится всегда — если рельеф выключен
                # или стал недоступен, используем плоский фолбэк (Z = целевая
                # высота съемки, как было до этой функции): так координаты
                # маршрута всегда 3D одинаково, а не то 2D, то 3D в
                # зависимости от результата запроса к внешнему сервису.
                # Длина этапов (и налёт) не меняется (v1-упрощение, без учета
                # наклонной дальности) — меняется только высота Z.
                try:
                    legs = _apply_terrain_profile(
                        legs, vehicle.launch_point, projector,
                        survey_geometry.height_m, settings.terrain_climb_angle_deg, active_provider,
                    )
                    if active_provider is real_elevation_provider:
                        terrain_applied = True
                except ElevationLookupError as exc:
                    if active_provider is real_elevation_provider:
                        terrain_unavailable_reason = str(exc)
                        _logger.warning(
                            "рельеф недоступен — высота остаётся абсолютной",
                            extra={"model_key": model_key, "camera_key": camera_key, "reason": terrain_unavailable_reason},
                        )
                        active_provider = ConstantElevationProvider(0.0)
                    legs = _apply_terrain_profile(
                        legs, vehicle.launch_point, projector,
                        survey_geometry.height_m, settings.terrain_climb_angle_deg, active_provider,
                    )

                sortie_legs[id(sortie)] = legs
                final_sorties.append(sortie)
            routing_result.sorties_by_vehicle[vehicle.id] = final_sorties
        if resplit_count:
            log.append(
                f"Обход зон удлинил переходы, и {resplit_count} вылет(ов) перестали укладываться в энергобюджет — "
                "их галсы перераспределены на дополнительные вылеты."
            )

        if terrain_unavailable_reason is not None:
            log.append(f"Рельеф недоступен ({terrain_unavailable_reason}) — высота держится абсолютной.")
        elif terrain_applied:
            log.append(
                f"Рельеф учтён: высота над поверхностью держится {survey_geometry.height_m:.1f} м с округлением "
                f"по углу набора/снижения {settings.terrain_climb_angle_deg:.0f}°, данные — Open Topo Data."
            )

        plan_sorties: list[PlanSortie] = []
        total_flight_s = 0.0
        plan_start: datetime | None = None
        plan_end: datetime | None = None

        # Борта одной площадки взлетают по очереди, а не в одну секунду (см.
        # schedule.DEFAULT_LAUNCH_INTERVAL_S): очередь считается только по
        # бортам, которым вообще достались вылеты.
        launches_at_site: dict[tuple[float, float], int] = {}
        scheduled_tracks: list[SortieTrack] = []
        deconflict_delays = 0
        for vehicle_index, vehicle in enumerate(vehicles):
            tick.span("Расписание вылетов", vehicle_index, len(vehicles), (0.80, 0.98))
            launch_name = launch_name_by_vehicle[vehicle.id]
            sorties = routing_result.sorties_by_vehicle.get(vehicle.id, [])
            site_key = (round(vehicle.launch_point.x, 1), round(vehicle.launch_point.y, 1))
            launch_rank = launches_at_site.get(site_key, 0)
            if sorties:
                launches_at_site[site_key] = launch_rank + 1
            # Разведение по времени: если расписание борта сближает его с уже
            # расписанным бортом ближе D_min, его вылеты сдвигаются на
            # интервал выпуска, пока конфликт не исчезнет (не больше
            # MAX_DECONFLICT_DELAYS раз). Раньше конфликтов не искали вовсе —
            # борта с соседних галсов на границе кластеров шли одновременно
            # в десятках метров друг от друга. Что не удалось развести,
            # честно отметит проверка безопасности («Разведение»).
            offset_s = launch_rank * DEFAULT_LAUNCH_INTERVAL_S
            for attempt in range(MAX_DECONFLICT_DELAYS + 1):
                try:
                    scheduled = assign_timestamps(
                        sorties, lat, lon, task.work_date,
                        window_start=task.window_start, window_end=task.window_end, tz=tz,
                        start_offset_s=offset_s,
                    )
                except ScheduleError as exc:
                    raise PlanInfeasibleError(str(exc)) from exc
                candidate_tracks = [
                    SortieTrack(vehicle.id, _route_line(sortie_legs[id(sched.sortie)]), sched.start_utc,
                                sched.end_utc, cruise_speed)
                    for sched in scheduled
                ]
                if attempt == MAX_DECONFLICT_DELAYS or not _conflicts(
                    candidate_tracks, scheduled_tracks, settings.separation_distance_m
                ):
                    break
                offset_s += DEFAULT_LAUNCH_INTERVAL_S
                deconflict_delays += 1
            scheduled_tracks.extend(candidate_tracks)

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
                        distance_m=leg.length_m, height_agl_m=leg.height_agl_m,
                    ))
                    cursor = phase_end
                if phases:
                    # sched.end_utc посчитан отдельно, из суммы длин за один
                    # раз, — синхронизируем последний этап явно, чтобы не
                    # осталось расхождения от разного порядка операций с float.
                    phases[-1] = phases[-1].model_copy(update={"end_utc": sched.end_utc})

                route_line_utm = LineString(route_coords)
                route_line_wgs84 = projector.to_wgs84(route_line_utm)
                # Из этапов вылета (не из исходной 2D-геометрии галсов) —
                # так съемочные галсы несут ту же высоту Z, что и облет
                # рельефа уже встроил в маршрут выше.
                survey_leg_coords = [leg.coords for leg in legs if leg.kind == "survey"]
                survey_tracks_wgs84 = projector.to_wgs84(MultiLineString(survey_leg_coords))
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

        if deconflict_delays:
            log.append(
                f"Разведение по времени: взлеты сдвинуты {deconflict_delays} раз(а) на "
                f"{DEFAULT_LAUNCH_INTERVAL_S:.0f} с, чтобы борта не сближались ближе "
                f"{settings.separation_distance_m:.0f} м."
            )
        j1_s = (plan_end - plan_start).total_seconds() if plan_start and plan_end else 0.0
        j2_s = total_flight_s + SORTIE_PENALTY_S * len(plan_sorties)

        if plan_start and plan_end:
            log.append(
                f"Расписание с учетом светового дня на {task.work_date.isoformat()} для координат "
                f"({lat:.2f}, {lon:.2f}): первый вылет в {plan_start.astimezone(tz or timezone.utc):%H:%M}, "
                f"последний закончится в {plan_end.astimezone(tz or timezone.utc):%H:%M} ({tz_label})."
            )
        log.append(
            f"Итог по кандидату «{model.name} + камера {camera.name}»: "
            f"J1 (общее время) = {_fmt_hm(j1_s)}, J2 (налет) = {_fmt_hm(j2_s)}."
        )

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
        if terrain_unavailable_reason is not None:
            warnings.append(
                f"рельеф недоступен ({terrain_unavailable_reason}) — высота держится абсолютной, "
                "не над поверхностью"
            )

        return _Candidate(
            model_key=model_key, camera_key=camera_key, model=model, survey_geometry=survey_geometry,
            cruise_speed=cruise_speed, budget_s=budget_s, plan_sorties=plan_sorties,
            j1_s=j1_s, j2_s=j2_s, warnings=warnings, log=log,
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

    best = _pick_best_candidate(candidates, task.criterion_alpha, task.criterion_mode)

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
        warnings=([adjustment_note] if adjustment_note else []) + best.warnings,
        model_key=best.model_key,
        camera_key=best.camera_key,
        height_m=best.survey_geometry.height_m,
        swath_m=best.survey_geometry.swath_m,
        cruise_speed_mps=best.cruise_speed,
        budget_s=best.budget_s,
        sorties=best.plan_sorties,
        calculation_log=intro_log + best.log,
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


def confirm_plan(plan_id: str, user: str, *, override_violations: bool = False) -> PlanSummary:
    """ЭКС.ФТ.6: ручное подтверждение плана оператором.

    Два условия, и они разной природы. Предусловие ЭКС.ФТ.2 — «последняя
    проверка безопасности выполнена и не содержит нарушений» — проверяется
    чтением отчёта: это правило предметной области, и его нарушение означает,
    что оператору вообще не следовало показывать кнопку. Само же изменение
    статуса делается условным ``UPDATE`` в репозитории: это защита от гонки
    (ЭКС.ФТ.9), а не от неверного состояния, и читать-проверять-писать здесь
    нельзя в принципе.

    ``override_violations`` — оператор отметил принятыми все нарушения
    последнего отчёта (расширение ЭКС.ФТ.2 по запросу пользователя). Тогда
    требование «без нарушений» снимается, но остальные остаются: проверка
    обязана быть выполнена и относиться именно к этой версии плана — иначе
    принимался бы риск, которого никто не измерял.
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
    if report.status != "Пройдена" and not override_violations:
        raise PlanNotConfirmableError(
            "план не подтверждается: последняя проверка безопасности содержит нарушения"
        )

    with_overrides = override_violations and report.status != "Пройдена"
    confirmed = repositories.plans.confirm(plan_id, user, with_overrides=with_overrides)
    if confirmed is None:
        actual = repositories.plans.get(plan_id)
        log.warning(
            "подтверждение отклонено — статус плана изменился",
            extra={"plan_id": plan_id, "plan_status": actual.status, "user": user},
        )
        raise PlanConfirmConflictError(actual)

    # ЭКС.ФТ.6: подтверждённый план фиксирует и задачу — править её параметры
    # больше нельзя. Новый расчёт (mark_calculated) снова разблокирует.
    task_service.mark_confirmed(confirmed.task_id)
    log.info(
        "статус плана изменен",
        extra={
            "plan_id": plan_id, "plan_status": confirmed.status, "user": user,
            "with_overrides": with_overrides,
        },
    )
    return _to_summary(confirmed)
