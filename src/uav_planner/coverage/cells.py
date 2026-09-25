"""Boustrophedon-декомпозиция рабочей области на ячейки без внутренних дыр.

Область режется горизонтальными (в системе координат, повернутой так, что
направление слайсинга совпадает с осью X) полосами по критическим Y —
координатам вершин полигона и его дыр. Полосы с одинаковой связностью (то же
число компонент, каждая однозначно соприкасается с компонентом предыдущей
полосы) объединяются в одну ячейку — так без явной классификации событий
IN/OUT/split/merge получается тот же результат, что и в классической
boustrophedon cellular decomposition (Choset & Pignon, 1997): каждая ячейка —
полигон без внутренних дыр, который можно полностью покрыть галсами.

Направление слайсинга выбирается один раз для всей рабочей области, а
направление галсов внутри каждой получившейся ячейки — заново, по минимальной
ширине именно этой ячейки (см. концепцию, раздел 4Б3). Если из-за этого в
ячейке останется вогнутость, не видимая слайсингу по глобальному направлению,
``generate_tracks`` (см. ``tracks.py``) просто вернет для такого галса
несколько отрезков вместо одного — область все равно покрыта полностью и без
выхода за ее границы, это не ошибка.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from shapely import affinity
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .direction import best_sweep_direction
from .errors import CoverageError

_STRIP_PAD_M = 1.0  # запас по x при вырезании горизонтальной полосы, м


@dataclass(frozen=True)
class Cell:
    """Ячейка декомпозиции: полигон без внутренних дыр + собственное направление галсов."""

    polygon: BaseGeometry
    direction_deg: float


def _as_polygons(geom: BaseGeometry) -> list[BaseGeometry]:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        result: list[BaseGeometry] = []
        for part in geom.geoms:
            result.extend(_as_polygons(part))
        return result
    return []  # линии/точки — не вносят площади, игнорируем


def _safe_union(a: BaseGeometry, b: BaseGeometry) -> Optional[BaseGeometry]:
    """Объединение открытой ячейки с компонентом новой полосы, если оно
    остается одним полигоном; ``None`` — не сливаются (касание в точке,
    численный зазор). Раньше из MultiPolygon молча бралась наибольшая часть,
    и остальная площадь выпадала из декомпозиции — нарушалось ⋃Cᵢ = W."""
    merged = unary_union([a, b])
    if not merged.is_valid:
        merged = merged.buffer(0)
    return merged if merged.geom_type == "Polygon" else None


def _match_components(
    open_cells: Sequence[BaseGeometry], components: Sequence[BaseGeometry]
) -> Optional[list[tuple[BaseGeometry, BaseGeometry]]]:
    """Сопоставляет каждую открытую ячейку ровно одному компоненту новой полосы
    (по пересечению/касанию). ``None`` — топология изменилась (появился,
    исчез, разделился или слился компонент), слияние не выполняется, текущие
    открытые ячейки нужно закрыть как готовые."""
    if len(open_cells) != len(components):
        return None

    used = [False] * len(components)
    pairs: list[tuple[BaseGeometry, BaseGeometry]] = []
    for oc in open_cells:
        match_idx = None
        for i, comp in enumerate(components):
            if used[i] or not oc.intersects(comp):
                continue
            if match_idx is not None:
                return None  # касается более одного компонента — неоднозначно
            match_idx = i
        if match_idx is None:
            return None
        used[match_idx] = True
        pairs.append((oc, components[match_idx]))
    return pairs


def _decompose_polygon(
    polygon: BaseGeometry, direction_deg: float, min_cell_area_m2: float
) -> list[BaseGeometry]:
    rotated = affinity.rotate(polygon, -direction_deg, origin=(0, 0), use_radians=False)
    if rotated.geom_type != "Polygon":
        raise CoverageError(f"ожидался Polygon после поворота, получено {rotated.geom_type}")

    ys = sorted(
        {y for _, y in rotated.exterior.coords}
        | {y for interior in rotated.interiors for _, y in interior.coords}
    )
    if len(ys) < 2:
        return []

    minx, _, maxx, _ = rotated.bounds
    open_cells: list[BaseGeometry] = []
    finished: list[BaseGeometry] = []

    for y0, y1 in zip(ys[:-1], ys[1:]):
        if y1 - y0 <= 0:
            continue
        strip = box(minx - _STRIP_PAD_M, y0, maxx + _STRIP_PAD_M, y1)
        piece = rotated.intersection(strip)
        components = [p for p in _as_polygons(piece) if p.area >= min_cell_area_m2]

        matched = _match_components(open_cells, components) if open_cells else None
        if matched is not None:
            next_open: list[BaseGeometry] = []
            for oc, comp in matched:
                merged = _safe_union(oc, comp)
                if merged is None:
                    finished.append(oc)  # не слилось — ячейка закрыта, компонент открывает новую
                    next_open.append(comp)
                else:
                    next_open.append(merged)
            open_cells = next_open
        else:
            finished.extend(open_cells)
            open_cells = components

    finished.extend(open_cells)
    return [
        affinity.rotate(c, direction_deg, origin=(0, 0), use_radians=False) for c in finished
    ]


def boustrophedon_cells(
    area: BaseGeometry,
    wind_bearing_deg: Optional[float] = None,
    min_cell_area_m2: float = 1.0,
) -> list[Cell]:
    """Декомпозирует рабочую область (полигон/мультиполигон, метры UTM) на
    ячейки без внутренних дыр.

    Направление слайсинга — минимальная ширина всей области (по частям
    мультиполигона отдельно, так как они физически не связаны); направление
    галсов в каждой получившейся ячейке — минимальная ширина именно этой
    ячейки. ``min_cell_area_m2`` отсекает численный мусор (тонкие сколы на
    вершинах) — практического покрытия они не несут.
    """
    if area is None or area.is_empty:
        return []

    cells: list[Cell] = []
    for part in _as_polygons(area):
        slicing_direction = best_sweep_direction(part, wind_bearing_deg)
        for cell_polygon in _decompose_polygon(part, slicing_direction, min_cell_area_m2):
            cell_direction = best_sweep_direction(cell_polygon, wind_bearing_deg)
            cells.append(Cell(polygon=cell_polygon, direction_deg=cell_direction))
    return cells
