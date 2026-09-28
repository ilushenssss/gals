"""Независимая проверка безопасности готового плана — восемь критериев: семь из
Таблицы 1 docs/trebovania/Проверка_безопасности.md (БЕЗ.ФТ.2), формально
описанных в Математическая_модель.md, раздел 15, и потолок высоты полета
(``check_max_altitude``).

Каждая функция — чистая геометрическая/временная проверка результата
(маршрутов, расписания, заявленных параметров съемки), а не внутренних
структур решателя — отсюда и «независимая»: модуль не переиспользует код
``routing``/``coverage``, только их заявленный результат.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from typing import Sequence

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner.camera import DEFAULT_MAX_ALTITUDE_M
from uav_planner.schedule import work_window_utc

DEFAULT_STEP_M = 20.0
DEFAULT_COVERAGE_TOLERANCE = 0.01
DEFAULT_MIN_SEPARATION_M = 50.0
# DEFAULT_MAX_ALTITUDE_M — единственный источник в uav_planner.camera.optics
# (там же теперь и ограничивается на этапе расчета геометрии съемки по GSD,
# по запросу пользователя); здесь просто переиспользуем то же число для
# независимой повторной проверки уже готового плана — не копия значения, а
# импорт, чтобы потолок не мог разойтись между расчетом и проверкой.
# Шаг выборки точек для проверки высоты над рельефом — заметно реже, чем
# DEFAULT_STEP_M у остальных проверок: запрос высоты рельефа — сетевой вызов
# (см. Ответы_экспертов_Геоскан.pdf, вопрос 13, «строить маршрут с шагом»),
# и гонять его каждые 20 м не нужно — резкий перепад рельефа виден и на этом
# шаге, а вот лишние сотни запросов на маршрут — не нужны.
DEFAULT_ALTITUDE_STEP_M = 300.0
_VIOLATION_LIMIT = 5


@dataclass(frozen=True)
class Violation:
    """Одно нарушение — текст причины и, где это осмысленно, точка на карте
    («опасный момент», в метрах UTM), чтобы интерфейс мог показать ее на
    карте и подсветить именно эту строку при наведении (см.
    web/src/features/safety/ViolationMarkers.tsx)."""

    message: str
    point: Point | None = None


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    violations: tuple[Violation, ...] = ()


@dataclass(frozen=True)
class SortieTrack:
    """Минимум, нужный для проверки разведения (БЕЗ.ФТ.2 · «Разведение»)."""

    uav_id: str
    route: BaseGeometry  # LineString, метры (UTM)
    start_utc: datetime
    end_utc: datetime
    cruise_speed_mps: float


def _finalize(name: str, violations: list[Violation], limit: int = _VIOLATION_LIMIT) -> CheckResult:
    unique: dict[str, Violation] = {}
    for v in violations:
        unique.setdefault(v.message, v)
    shown = list(unique.values())[:limit]
    if len(unique) > limit:
        shown.append(Violation(f"...и еще {len(unique) - limit} нарушени(й)"))
    return CheckResult(name=name, passed=not violations, violations=tuple(shown))


def discretize(line: BaseGeometry, step_m: float = DEFAULT_STEP_M) -> list[Point]:
    """Точки вдоль ``line`` с шагом ``step_m`` (метры), включая оба конца."""
    length = line.length
    if length == 0:
        return [Point(line.coords[0])]
    n = max(1, math.ceil(length / step_m))
    return [line.interpolate(min(i * step_m, length)) for i in range(n + 1)]


def check_geozones(
    route: BaseGeometry,
    no_fly_footprints: list[BaseGeometry],
    obstacle_footprints: list[BaseGeometry],
    step_m: float = DEFAULT_STEP_M,
) -> CheckResult:
    """Траектория не пересекает бесполетные зоны и высотные препятствия."""
    violations: list[Violation] = []
    for point in discretize(route, step_m):
        for zone in no_fly_footprints:
            if zone.intersects(point):
                violations.append(Violation(
                    f"маршрут пересекает бесполетную зону в точке ({point.x:.0f}, {point.y:.0f})", point
                ))
                break
        for obstacle in obstacle_footprints:
            if obstacle.intersects(point):
                violations.append(Violation(
                    f"маршрут пересекает высотное препятствие в точке ({point.x:.0f}, {point.y:.0f})", point
                ))
                break
    return _finalize("geozones", violations)


def check_allowed_space(
    route: BaseGeometry,
    allowed_union: BaseGeometry,
    step_m: float = DEFAULT_STEP_M,
) -> CheckResult:
    """Траектория находится внутри разрешенного воздушного пространства."""
    violations: list[Violation] = []
    for point in discretize(route, step_m):
        if not allowed_union.covers(point):
            violations.append(Violation(
                f"точка ({point.x:.0f}, {point.y:.0f}) вне разрешенного воздушного пространства", point
            ))
    return _finalize("airspace", violations)


def check_energy(
    route_length_m: float, cruise_speed_mps: float, budget_s: float, route: BaseGeometry | None = None
) -> CheckResult:
    """Фактическое время вылета (маршрут целиком, включая переходы) не
    превышает энергобюджет — независимая проверка того, что заложено в
    ``routing``/``plan_service``: те тоже учитывают переходы при подборе
    галсов и налете (``routing.cluster_assign_and_route``,
    ``plan_service._build_sortie_legs``), но при кластеризации/маршрутизации
    переходы оцениваются по прямой, а не по фактическому маршруту в обход
    зон — обход (``visibility.find_path``) обычно чуть длиннее прямой, и в
    редких случаях это может вытолкнуть уже собранный вылет за бюджет уже
    после того, как Split счел его допустимым. Эта проверка сверяет именно
    фактический, а не оценочный маршрут — узнать реальный расход энергии.
    ``route`` — опционально, только чтобы отметить на карте точку «здесь
    закончится энергобюджет» (маршрут при этом не изменяется)."""
    required_s = route_length_m / cruise_speed_mps
    if required_s > budget_s:
        deficit_s = required_s - budget_s
        point = route.interpolate(min(budget_s * cruise_speed_mps, route.length)) if route is not None else None
        return CheckResult("energy", False, (
            Violation(
                f"фактическое время вылета с учетом переходов ({required_s / 60:.1f} мин) "
                f"превышает бюджет ({budget_s / 60:.1f} мин) на {deficit_s / 60:.1f} мин",
                point,
            ),
        ))
    return CheckResult("energy", True)


def check_max_altitude(
    height_m: float,
    limit_m: float = DEFAULT_MAX_ALTITUDE_M,
    agl_by_point: list[tuple[Point, float]] | None = None,
) -> CheckResult:
    """Высота полета не выше жесткого потолка (по умолчанию 150 м) — над
    поверхностью (AGL), не абсолютная (см. Ответы_экспертов_Геоскан.pdf,
    вопрос 13: высота задается именно над поверхностью).

    ``agl_by_point`` — независимо пересчитанная высота над рельефом в
    точках маршрута: свой запрос к провайдеру высот (см.
    ``services.safety_service``), не переиспользующий профиль, который уже
    посчитал ``plan_service`` при построении маршрута — тот же принцип
    независимости, что и у остальных проверок этого модуля. Если рельеф не
    учтен (плана без 3D-маршрута или рельеф был недоступен и при расчете —
    см. ``PlanDetail.warnings``) — ``None``, и проверка честно деградирует
    до сравнения одного числа ``height_m`` (v1-ограничение: высота считается
    одной и той же над всем вылетом)."""
    if agl_by_point is not None:
        violations = tuple(
            Violation(
                f"высота над поверхностью в точке маршрута ({agl:.0f} м) выше допустимого потолка ({limit_m:.0f} м)",
                point,
            )
            for point, agl in agl_by_point
            if agl > limit_m
        )
        return CheckResult("altitude", not violations, violations)
    if height_m > limit_m:
        return CheckResult("altitude", False, (
            Violation(f"высота полета ({height_m:.0f} м) выше допустимого потолка ({limit_m:.0f} м)"),
        ))
    return CheckResult("altitude", True)


def check_reachability(
    route: BaseGeometry,
    landing_points: list[Point],
    cruise_speed_mps: float,
    budget_s: float,
    step_m: float = DEFAULT_STEP_M,
) -> CheckResult:
    """Из любой точки маршрута достижима хотя бы одна площадка посадки с
    учетом остатка энергобюджета в этой точке."""
    if not landing_points:
        return CheckResult("reachability", False, (
            Violation("нет ни одной площадки посадки или резервной площадки", Point(route.coords[0])),
        ))

    violations: list[Violation] = []
    for point in discretize(route, step_m):
        elapsed_s = route.project(point) / cruise_speed_mps
        remaining_s = budget_s - elapsed_s
        nearest_m = min(point.distance(site) for site in landing_points)
        time_to_site_s = nearest_m / cruise_speed_mps
        if remaining_s < time_to_site_s:
            violations.append(Violation(
                f"на {route.project(point):.0f} м маршрута остатка ресурса "
                f"({remaining_s / 60:.1f} мин) не хватит на долет до ближайшей площадки "
                f"({time_to_site_s / 60:.1f} мин)",
                point,
            ))
    return _finalize("reachability", violations)


def check_coverage(
    survey_tracks: list[BaseGeometry],
    working_area: BaseGeometry,
    swath_m: float | Sequence[float],
    tolerance: float = DEFAULT_COVERAGE_TOLERANCE,
) -> CheckResult:
    """Рабочая область покрыта полностью (с допуском на численный мусор).

    ``swath_m`` — одна полоса захвата на все галсы или своя у каждого галса
    (по порядку ``survey_tracks``): в смешанном парке галсы разных моделей
    сняты с разной высоты."""
    if working_area.is_empty or working_area.area == 0:
        return CheckResult("coverage", True)

    swaths = [swath_m] * len(survey_tracks) if isinstance(swath_m, (int, float)) else list(swath_m)
    if len(swaths) != len(survey_tracks):
        raise ValueError("число полос захвата не совпадает с числом галсов")
    covered = (
        unary_union([t.buffer(w / 2) for t, w in zip(survey_tracks, swaths)]) if survey_tracks else Polygon()
    )
    uncovered = working_area.difference(covered)
    uncovered_fraction = uncovered.area / working_area.area
    if uncovered_fraction > tolerance:
        point = uncovered.representative_point() if not uncovered.is_empty else None
        return CheckResult("coverage", False, (
            Violation(
                f"не покрыто {uncovered_fraction * 100:.1f}% рабочей области (допуск {tolerance * 100:.0f}%)",
                point,
            ),
        ))
    return CheckResult("coverage", True)


def check_daylight(
    start_utc: datetime,
    end_utc: datetime,
    lat: float,
    lon: float,
    window_start: time | None = None,
    window_end: time | None = None,
    tz: tzinfo | None = None,
    location: Point | None = None,
) -> CheckResult:
    """Вылет укладывается в действующее окно работ (окно оператора в местном
    времени ``tz`` ∩ световой день) одного из местных дней, на которые
    приходится вылет. Окно считает та же ``schedule.work_window_utc``, что и
    расписание: формула светового дня одна, а независимость проверки — в
    том, что сверяется уже готовое время вылета, а не повторяется расчет
    расписания. ``location`` — опционально, точка вылета в UTM, только для
    отметки на карте (нарушение привязано ко всему вылету)."""
    local_day = start_utc.astimezone(tz).date() if tz is not None else start_utc.date()
    # Соседние дни — на случай, когда пояс задачи далек от «солнечного» (UTC
    # для старых задач на восточных долготах): световой день местного дня D
    # начинается тогда в UTC-сутках D−1.
    windows = [
        w for w in (
            work_window_utc(lat, lon, local_day + timedelta(days=shift), window_start, window_end, tz)
            for shift in (-1, 0, 1)
        )
        if w is not None
    ]
    if not windows:
        return CheckResult("daylight", False, (
            Violation("на дату вылета нет рабочих часов — полярная ночь или окно работ вне светового дня", location),
        ))
    if any(win_start <= start_utc and end_utc <= win_end for win_start, win_end in windows):
        return CheckResult("daylight", True)

    nearest_start, nearest_end = min(windows, key=lambda w: abs((w[0] - start_utc).total_seconds()))
    return CheckResult("daylight", False, (
        Violation(
            f"вылет {start_utc.isoformat()}–{end_utc.isoformat()} выходит за пределы "
            f"светового дня {nearest_start.isoformat()}–{nearest_end.isoformat()}",
            location,
        ),
    ))


def _vertex_times(track: SortieTrack) -> list[tuple[float, tuple[float, float]]]:
    """Моменты прохода вершин маршрута (секунды от начала вылета) при
    постоянной крейсерской скорости — та же модель движения, что у
    расписания (``plan_service``: длительность этапа = длина / скорость)."""
    coords = [(c[0], c[1]) for c in track.route.coords]
    result = [(0.0, coords[0])]
    travelled = 0.0
    for a, b in zip(coords, coords[1:]):
        travelled += math.hypot(b[0] - a[0], b[1] - a[1])
        result.append((travelled / track.cruise_speed_mps, b))
    return result


def _position(vertices: list[tuple[float, tuple[float, float]]], times: list[float], t: float) -> tuple[float, float]:
    """Положение борта в момент ``t`` секунд от начала вылета (до начала — в
    первой точке, после конца маршрута — в последней). ``times`` — моменты
    вершин из ``vertices``, для бинарного поиска отрезка."""
    if t <= times[0]:
        return vertices[0][1]
    if t >= times[-1]:
        return vertices[-1][1]
    i = bisect.bisect_right(times, t)
    (t0, p0), (t1, p1) = vertices[i - 1], vertices[i]
    k = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
    return (p0[0] + (p1[0] - p0[0]) * k, p0[1] + (p1[1] - p0[1]) * k)


def _closest_approach(a: SortieTrack, b: SortieTrack) -> tuple[float, datetime, Point] | None:
    """Точный минимум расстояния между двумя бортами на общем интервале
    времени: ``(расстояние, момент, середина отрезка между бортами)``.

    Оба маршрута — ломаные, пройденные с постоянной скоростью, поэтому между
    соседними «изломами» (моментами прохода вершин любого из двух маршрутов)
    вектор между бортами меняется линейно, и минимум его длины на таком
    отрезке времени находится в закрытой форме. Раньше расстояние
    сравнивалось раз в 30 с — при встречном движении 2×20 м/с это отсчеты
    через 1200 м, и сближение ближе порога между ними проходило незамеченным.
    """
    overlap_start = max(a.start_utc, b.start_utc)
    overlap_end = min(a.end_utc, b.end_utc)
    if overlap_start >= overlap_end:
        return None

    va, vb = _vertex_times(a), _vertex_times(b)
    ta, tb = [t for t, _ in va], [t for t, _ in vb]
    span_s = (overlap_end - overlap_start).total_seconds()
    offset_a = (overlap_start - a.start_utc).total_seconds()
    offset_b = (overlap_start - b.start_utc).total_seconds()
    breaks = {0.0, span_s}
    breaks.update(t - offset_a for t, _ in va if 0.0 < t - offset_a < span_s)
    breaks.update(t - offset_b for t, _ in vb if 0.0 < t - offset_b < span_s)
    times = sorted(breaks)

    best: tuple[float, float, tuple[float, float], tuple[float, float]] | None = None
    for t0, t1 in zip(times, times[1:]):
        pa0, pa1 = _position(va, ta, t0 + offset_a), _position(va, ta, t1 + offset_a)
        pb0, pb1 = _position(vb, tb, t0 + offset_b), _position(vb, tb, t1 + offset_b)
        r0 = (pb0[0] - pa0[0], pb0[1] - pa0[1])
        dr = ((pb1[0] - pa1[0]) - r0[0], (pb1[1] - pa1[1]) - r0[1])
        dr2 = dr[0] ** 2 + dr[1] ** 2
        k = 0.0 if dr2 == 0 else min(max(-(r0[0] * dr[0] + r0[1] * dr[1]) / dr2, 0.0), 1.0)
        distance = math.hypot(r0[0] + dr[0] * k, r0[1] + dr[1] * k)
        if best is None or distance < best[0]:
            pa = (pa0[0] + (pa1[0] - pa0[0]) * k, pa0[1] + (pa1[1] - pa0[1]) * k)
            pb = (pb0[0] + (pb1[0] - pb0[0]) * k, pb0[1] + (pb1[1] - pb0[1]) * k)
            best = (distance, t0 + (t1 - t0) * k, pa, pb)

    distance, t, pa, pb = best
    midpoint = Point((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    return distance, overlap_start + timedelta(seconds=t), midpoint


def check_separation(
    sorties: list[SortieTrack],
    min_separation_m: float = DEFAULT_MIN_SEPARATION_M,
) -> CheckResult:
    """Нет сближений разных БВС на одной высоте (в v1 — единой для всего
    плана) ближе ``min_separation_m`` в перекрывающиеся по времени интервалы.
    Для каждой пары вылетов ищется точный момент наибольшего сближения (см.
    ``_closest_approach``), а не выборка по времени."""
    violations: list[Violation] = []
    for i in range(len(sorties)):
        for j in range(i + 1, len(sorties)):
            a, b = sorties[i], sorties[j]
            if a.uav_id == b.uav_id:
                continue
            approach = _closest_approach(a, b)
            if approach is None:
                continue
            distance, moment, midpoint = approach
            if distance < min_separation_m:
                violations.append(Violation(
                    f"{a.uav_id} и {b.uav_id} сближаются до {distance:.0f} м "
                    f"(порог {min_separation_m:.0f} м) около {moment.isoformat()}",
                    midpoint,
                ))
    return _finalize("separation", violations)
