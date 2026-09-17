"""Направление галсов по минимальной ширине, со штрафом за боковой ветер.

Для выпуклого многоугольника оптимальное направление галсов параллельно одной
из его сторон (Huang, 2001) — минимизирует ширину поперек галсов и тем самым
число разворотов. Поэтому достаточно перебрать направления сторон выпуклой
оболочки (вращающиеся калиперы), а не все возможные углы. См. «Методы»,
раздел 5, и «Архитектура кода», модуль ``coverage``.
"""

from __future__ import annotations

import math
from typing import Optional

from shapely.geometry.base import BaseGeometry


def _width_along(coords: list[tuple[float, float]], direction_deg: float) -> float:
    """Протяженность точек ``coords`` вдоль оси, перпендикулярной направлению
    ``direction_deg`` — «ширина» фигуры поперек галсов этого направления."""
    rad = math.radians(direction_deg + 90.0)
    ux, uy = math.cos(rad), math.sin(rad)
    projections = [x * ux + y * uy for x, y in coords]
    return max(projections) - min(projections)


def crosswind_fraction(direction_deg: float, wind_bearing_deg: float) -> float:
    """Доля ветра, действующая поперек направления галсов: 0 — ветер вдоль
    галсов, 1 — ветер строго поперек."""
    return abs(math.sin(math.radians(direction_deg - wind_bearing_deg)))


def best_sweep_direction(
    polygon: BaseGeometry,
    wind_bearing_deg: Optional[float] = None,
    wind_penalty_weight: float = 0.15,
) -> float:
    """Направление галсов (градусы, 0..180 от оси X против часовой стрелки),
    минимизирующее стоимость ``width * (1 + wind_penalty_weight * crosswind_fraction)``.

    Без ``wind_bearing_deg`` — чистая минимальная ширина. Направление считается
    по выпуклой оболочке ``polygon``: перебираются углы всех ее сторон.
    """
    hull = polygon.convex_hull
    if hull.geom_type != "Polygon" or len(hull.exterior.coords) < 4:
        return 0.0  # точка/отрезок/вырожденная фигура — направление не важно

    coords = list(hull.exterior.coords)
    candidate_angles = sorted(
        {
            round(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0, 9)
            for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:])
        }
    )

    best_angle, best_cost = 0.0, math.inf
    for angle in candidate_angles:
        width = _width_along(coords, angle)
        cost = width
        if wind_bearing_deg is not None:
            cost *= 1.0 + wind_penalty_weight * crosswind_fraction(angle, wind_bearing_deg)
        if cost < best_cost:
            best_cost, best_angle = cost, angle
    return best_angle
