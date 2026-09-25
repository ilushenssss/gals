"""Рабочая область: буферы безопасности, пересечения/разности полигонов.

Рабочая область = (Область съемки ∩ Разрешенное пространство) − БПЗ − препятствия
выше съемочной высоты H (см. концепцию решения, раздел 4, шаг Б3).

Все геометрии, принимаемые и возвращаемые функциями этого модуля, — в метрической
проекции (UTM); перевод в/из WGS-84 — через :class:`uav_planner.geometry.projection.Projector`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import explain_validity

from .errors import GeometryError


@dataclass(frozen=True)
class HeightRange:
    """Диапазон высот [h_min, h_max] в метрах (над поверхностью)."""

    h_min: float
    h_max: float

    def __post_init__(self) -> None:
        if self.h_min > self.h_max:
            raise ValueError(f"h_min ({self.h_min}) больше h_max ({self.h_max})")

    def contains(self, h: float) -> bool:
        return self.h_min <= h <= self.h_max


@dataclass(frozen=True)
class TimeWindow:
    """Интервал действия зоны [start, end]."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("начало интервала позже конца")

    def contains(self, when: datetime) -> bool:
        return self.start <= when <= self.end

    def covers(self, start: datetime, end: datetime) -> bool:
        """Интервал целиком внутри окна."""
        return self.start <= start and end <= self.end

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Интервал хотя бы частично пересекается с окном."""
        return self.start < end and start < self.end


def _is_active_at(active_windows: Optional[Sequence[TimeWindow]], when: Optional[datetime]) -> bool:
    """Зона без заданных интервалов действует всегда. При запросе без момента
    времени (``when is None``) зона тоже считается активной — консервативно,
    как условлено с экспертами: интервал действия проверяется на этапе
    планирования вылета, а не при построении статичной рабочей области.
    """
    if not active_windows or when is None:
        return True
    return any(window.contains(when) for window in active_windows)


@dataclass(frozen=True)
class AllowedZone:
    """Зона разрешенного воздушного пространства: полигон, диапазон высот,
    интервалы действия (по аналогии с режимами ОрВД)."""

    id: str
    polygon: BaseGeometry
    height: HeightRange
    active_windows: Optional[Sequence[TimeWindow]] = None

    def is_active_at(self, when: Optional[datetime] = None) -> bool:
        return _is_active_at(self.active_windows, when)

    def is_active_throughout(self, start: datetime, end: datetime) -> bool:
        """Разрешенная зона годится для полета на интервале, только если она
        действует на нем целиком (зона без интервалов — всегда)."""
        return not self.active_windows or any(w.covers(start, end) for w in self.active_windows)


@dataclass(frozen=True)
class NoFlyZone:
    """Бесполетная зона с буфером безопасности и (опционально) интервалами действия."""

    id: str
    polygon: BaseGeometry
    safety_buffer_m: float = 0.0
    active_windows: Optional[Sequence[TimeWindow]] = None

    def footprint(self) -> BaseGeometry:
        """Контур с буфером безопасности — то, что вычитается из рабочей области."""
        if self.safety_buffer_m > 0:
            return self.polygon.buffer(self.safety_buffer_m)
        return self.polygon

    def is_active_at(self, when: Optional[datetime] = None) -> bool:
        return _is_active_at(self.active_windows, when)

    def is_active_during(self, start: datetime, end: datetime) -> bool:
        """БПЗ запрещает полет на интервале, если действует хотя бы в какой-то
        его момент (зона без интервалов — всегда)."""
        return not self.active_windows or any(w.overlaps(start, end) for w in self.active_windows)


@dataclass(frozen=True)
class Obstacle:
    """Высотное препятствие — призма «полигон + диапазон высот»."""

    id: str
    polygon: BaseGeometry
    height: HeightRange
    safety_buffer_m: float = 0.0

    def footprint(self) -> BaseGeometry:
        if self.safety_buffer_m > 0:
            return self.polygon.buffer(self.safety_buffer_m)
        return self.polygon

    def is_hole_at(self, survey_height_m: float, margin_m: float = 0.0) -> bool:
        """Препятствие становится дырой в рабочей области, если оно (с запасом)
        не ниже съемочной высоты H."""
        return (self.height.h_max + margin_m) >= survey_height_m


def validate_polygon(geom: BaseGeometry) -> BaseGeometry:
    """Проверяет валидность геометрии (в т.ч. отсутствие самопересечений —
    самопересекающийся полигон невалиден по определению shapely/OGC)."""
    if geom is None or geom.is_empty:
        raise GeometryError("геометрия пуста")
    if not geom.is_valid:
        raise GeometryError(f"геометрия невалидна: {explain_validity(geom)}")
    return geom


def safe_simplify(polygon: BaseGeometry, tolerance_m: float, safety_buffer_m: float) -> BaseGeometry:
    """Упрощение Дугласа — Пекера с допуском меньше буфера безопасности,
    чтобы упрощенный полигон не вышел за пределы буфера (см. «Методы», раздел 3)."""
    if safety_buffer_m > 0 and tolerance_m >= safety_buffer_m:
        raise ValueError(
            f"допуск упрощения ({tolerance_m} м) должен быть меньше буфера безопасности "
            f"({safety_buffer_m} м)"
        )
    return polygon.simplify(tolerance_m, preserve_topology=True)


def _subtract_footprints(base: BaseGeometry, footprints: Sequence[BaseGeometry]) -> BaseGeometry:
    """Вычитает из ``base`` все геометрии из ``footprints``, которые его пересекают.
    STRtree отсеивает заведомо непересекающиеся зоны — это быстрее последовательных
    ``.difference`` по всем зонам сцены (см. «Методы», раздел 3)."""
    if not footprints or base.is_empty:
        return base
    tree = STRtree(footprints)
    hit_idx = tree.query(base, predicate="intersects")
    working = base
    for i in hit_idx:
        if working.is_empty:
            break
        working = working.difference(footprints[i])
    return working


def compute_working_area(
    survey_area: BaseGeometry,
    allowed_zones: Sequence[AllowedZone],
    no_fly_zones: Sequence[NoFlyZone],
    obstacles: Sequence[Obstacle],
    survey_height_m: float,
    obstacle_margin_m: float = 0.0,
    when: Optional[datetime] = None,
) -> BaseGeometry:
    """Рабочая область = (Область ∩ Разрешенное) − БПЗ − препятствия выше H.

    Геометрии — в метрической проекции (UTM). ``survey_height_m`` — съемочная
    высота H (см. «Методы», раздел 4); ``obstacle_margin_m`` — запас безопасности
    по высоте, с которым препятствие считается дырой в области.
    """
    validate_polygon(survey_area)

    working = survey_area
    if allowed_zones:
        active = [
            zone.polygon
            for zone in allowed_zones
            if zone.height.contains(survey_height_m) and zone.is_active_at(when)
        ]
        if not active:
            raise GeometryError(
                "съемочная высота не покрыта ни одной активной зоной разрешенного "
                "воздушного пространства"
            )
        working = working.intersection(unary_union(active))

    no_fly_footprints = [zone.footprint() for zone in no_fly_zones if zone.is_active_at(when)]
    working = _subtract_footprints(working, no_fly_footprints)

    obstacle_footprints = [
        obstacle.footprint()
        for obstacle in obstacles
        if obstacle.is_hole_at(survey_height_m, obstacle_margin_m)
    ]
    working = _subtract_footprints(working, obstacle_footprints)

    working = _remove_enclosed_pockets(working, no_fly_footprints + obstacle_footprints)

    return working


def _remove_enclosed_pockets(
    working: BaseGeometry, restricted_footprints: Sequence[BaseGeometry]
) -> BaseGeometry:
    """Убирает из ``working`` куски, до которых нельзя долететь без пересечения
    запретной зоны/препятствия — например, «безопасное ядро» кольцевой БПЗ,
    целиком окружённое запретом (по запросу пользователя: такой участок
    должен остаться непокрытым, а не соединяться с остальной рабочей
    областью прямым переходом через запрет, как раньше).

    Геометрически: если объединение запретных зон само образует замкнутое
    кольцо (одна зона с дыркой, либо несколько зон, чьё объединение сомкнулось
    без просвета), у объединения появляется внутреннее кольцо (``interiors``)
    — область внутри него недостижима снаружи, не пересекая границу. Реальная
    достижимость с конкретной площадки вылета (граф видимости с учётом
    маршрута конкретного борта) — отдельная, более тяжёлая задача; здесь —
    более простой и всегда корректный частный случай: полностью замкнутый
    просвет исключается независимо от того, откуда потом полетит БВС.
    """
    if working.is_empty or not restricted_footprints:
        return working

    merged = unary_union(restricted_footprints)
    polygons = merged.geoms if hasattr(merged, "geoms") else [merged]
    pockets = [Polygon(ring) for poly in polygons for ring in getattr(poly, "interiors", [])]
    if not pockets:
        return working
    return _subtract_footprints(working, pockets)
