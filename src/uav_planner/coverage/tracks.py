"""Генерация галсов внутри ячейки и разбиение длинных галсов на части под вылет.

См. «Архитектура кода», модуль ``coverage``, и концепцию, раздел 4Б3.
"""

from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring

from .cells import Cell
from .errors import CoverageError


def _flatten_lines(geom: BaseGeometry) -> list[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        result: list[LineString] = []
        for part in geom.geoms:
            result.extend(_flatten_lines(part))
        return result
    return []  # касание в точке — реального галса там нет


def generate_tracks(cell: Cell, spacing_m: float) -> list[LineString]:
    """Параллельные галсы внутри ``cell`` с шагом ``spacing_m`` вдоль направления
    ``cell.direction_deg``, обрезанные по фактической границе ячейки.

    Если ячейка не идеально выпукла в этом направлении, для одной позиции
    может получиться несколько отрезков вместо одного — оба варианта покрывают
    ячейку без выхода за ее пределы, это допустимо.
    """
    if spacing_m <= 0:
        raise CoverageError(f"spacing_m должен быть положительным, получено {spacing_m}")

    polygon = cell.polygon
    direction = cell.direction_deg
    rotated = affinity.rotate(polygon, -direction, origin=(0, 0), use_radians=False)
    minx, miny, maxx, maxy = rotated.bounds
    if maxy <= miny:
        return []

    offsets: list[float] = []
    y = miny + spacing_m / 2.0
    while y <= maxy:
        offsets.append(y)
        y += spacing_m
    if not offsets:
        offsets.append((miny + maxy) / 2.0)
    elif (maxy - offsets[-1]) > spacing_m / 2.0 + 1e-9:
        offsets.append(maxy - spacing_m / 2.0)  # покрыть дальний край ячейки

    pad = max(1.0, (maxx - minx) * 0.01)
    tracks: list[LineString] = []
    for y0 in offsets:
        probe = LineString([(minx - pad, y0), (maxx + pad, y0)])
        tracks.extend(_flatten_lines(probe.intersection(rotated)))

    return [affinity.rotate(t, direction, origin=(0, 0), use_radians=False) for t in tracks]


def split_long_track(track: LineString, max_len_m: float) -> list[LineString]:
    """Режет галс на части не длиннее ``max_len_m`` (равными по длине кусками,
    чтобы избежать одного короткого «хвостового» отрезка)."""
    if max_len_m <= 0:
        raise CoverageError(f"max_len_m должен быть положительным, получено {max_len_m}")

    length = track.length
    if length <= max_len_m:
        return [track]

    n_parts = math.ceil(length / max_len_m)
    part_len = length / n_parts
    parts = []
    for i in range(n_parts):
        start = i * part_len
        end = length if i == n_parts - 1 else (i + 1) * part_len
        parts.append(substring(track, start, end))
    return parts
