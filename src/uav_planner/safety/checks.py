"""Независимая проверка безопасности готового плана — семь критериев из
Таблицы 1 docs/trebovania/Проверка_безопасности.md (БЕЗ.ФТ.2), формально
описанных в Математическая_модель.md, раздел 15.

Каждая функция — чистая геометрическая/временная проверка результата
(маршрутов, расписания, заявленных параметров съемки), а не внутренних
структур решателя — отсюда и «независимая»: модуль не переиспользует код
``routing``/``coverage``, только их заявленный результат.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner.schedule import daylight_window_utc_hours

DEFAULT_STEP_M = 20.0
DEFAULT_COVERAGE_TOLERANCE = 0.01
DEFAULT_MIN_SEPARATION_M = 50.0
DEFAULT_SEPARATION_TIME_STEP_S = 30.0
# Потолок высоты полета без специального разрешения (практический предел
# эксплуатации БВС в неклассифицированном пространстве, а не паспортный
# потолок конкретной модели).
DEFAULT_MAX_ALTITUDE_M = 150.0
_VIOLATION_LIMIT = 5


@dataclass(frozen=True)
class Violation:
    """Одно нарушение — текст причины и, где это осмысленно, точка на карте
    («опасный момент», в метрах UTM), чтобы интерфейс мог показать ее на
    карте и подсветить именно эту строку при наведении (см. static/index.html,
    Экран 5)."""

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


def check_max_altitude(height_m: float, limit_m: float = DEFAULT_MAX_ALTITUDE_M) -> CheckResult:
    """Высота съемки не выше жесткого потолка (по умолчанию 150 м).

    v1-ограничение (честно, не молча): план сейчас летит на одной постоянной
    высоте ``H`` весь вылет — переходы и галсы, без следования рельефу (см.
    docs/realization/"Multiple fixed-wing UAVs collaborative coverage
    3D.pdf", раздел 3.4 — altitude descent algorithm с цифровой моделью
    высот, которого у нас нет). Поэтому проверка сравнивает одно число, а не
    идет по точкам маршрута, как ``check_geozones``/``check_reachability`` —
    по точкам здесь пока нечего различать, высота везде одна и та же."""
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
    swath_m: float,
    tolerance: float = DEFAULT_COVERAGE_TOLERANCE,
) -> CheckResult:
    """Рабочая область покрыта полностью (с допуском на численный мусор)."""
    if working_area.is_empty or working_area.area == 0:
        return CheckResult("coverage", True)

    covered = unary_union([t.buffer(swath_m / 2) for t in survey_tracks]) if survey_tracks else Polygon()
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
    window_start_hour: float = 0.0,
    window_end_hour: float = 24.0,
    location: Point | None = None,
) -> CheckResult:
    """Вылет укладывается в световой день для даты и координат задачи.
    ``location`` — опционально, точка вылета в UTM, только для отметки на
    карте (нарушение здесь привязано ко всему вылету, а не к точке маршрута)."""
    window = daylight_window_utc_hours(lat, lon, start_utc.date())
    if window is None:
        return CheckResult("daylight", False, (
            Violation("полярная ночь на дату вылета — светового дня нет", location),
        ))

    day_start_h = max(window_start_hour, window[0])
    day_end_h = min(window_end_hour, window[1])
    base = datetime(start_utc.year, start_utc.month, start_utc.day, tzinfo=timezone.utc)
    win_start = base + timedelta(hours=day_start_h)
    win_end = base + timedelta(hours=day_end_h)

    if start_utc < win_start or end_utc > win_end:
        return CheckResult("daylight", False, (
            Violation(
                f"вылет {start_utc.isoformat()}–{end_utc.isoformat()} выходит за пределы "
                f"светового дня {win_start.isoformat()}–{win_end.isoformat()}",
                location,
            ),
        ))
    return CheckResult("daylight", True)


def _position_at(route: BaseGeometry, elapsed_s: float, cruise_speed_mps: float) -> Point:
    distance_m = min(max(elapsed_s, 0.0) * cruise_speed_mps, route.length)
    return route.interpolate(distance_m)


def check_separation(
    sorties: list[SortieTrack],
    min_separation_m: float = DEFAULT_MIN_SEPARATION_M,
    time_step_s: float = DEFAULT_SEPARATION_TIME_STEP_S,
) -> CheckResult:
    """Нет сближений разных БВС на одной высоте (в v1 — единой для всего
    плана) ближе ``min_separation_m`` в перекрывающиеся по времени интервалы."""
    violations: list[Violation] = []
    for i in range(len(sorties)):
        for j in range(i + 1, len(sorties)):
            a, b = sorties[i], sorties[j]
            if a.uav_id == b.uav_id:
                continue
            overlap_start = max(a.start_utc, b.start_utc)
            overlap_end = min(a.end_utc, b.end_utc)
            if overlap_start >= overlap_end:
                continue

            t = overlap_start
            while t <= overlap_end:
                pa = _position_at(a.route, (t - a.start_utc).total_seconds(), a.cruise_speed_mps)
                pb = _position_at(b.route, (t - b.start_utc).total_seconds(), b.cruise_speed_mps)
                distance = pa.distance(pb)
                if distance < min_separation_m:
                    midpoint = Point((pa.x + pb.x) / 2, (pa.y + pb.y) / 2)
                    violations.append(Violation(
                        f"{a.uav_id} и {b.uav_id} сближаются до {distance:.0f} м "
                        f"(порог {min_separation_m:.0f} м) около {t.isoformat()}",
                        midpoint,
                    ))
                    break
                t += timedelta(seconds=time_step_s)
    return _finalize("separation", violations)
