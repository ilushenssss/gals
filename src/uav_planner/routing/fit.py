"""Подгонка галсов под возможности бортов — до кластеризации.

``coverage`` строит галсы по геометрии ячейки и режет их только по паспортной
дальности маршрута модели (``max_route_km``, у части моделей ее нет вовсе).
Энергобюджет вылета и дальность связи там не видны, поэтому галс, который
длиннее того, что борт пролетит за один вылет (с переходами туда и обратно),
или уходит за радиус связи, Шаг 2 молча отправлял в «нераспределенные» — хотя
его части выполнимы. Здесь такие галсы режутся:

1. **Связь.** Если у всех бортов задана дальность связи, галс обрезается по
   объединению кругов связи их площадок; то, что снаружи, не выполнит никто —
   это возвращается отдельно, чтобы план честно назвал причину.
2. **Энергия.** Галс, который ни один борт не выполнит отдельным вылетом,
   режется на n равных частей с наименьшим n, при котором каждая часть по
   силам хоть одному борту. Если такого n нет (часть галса дальше, чем
   борт долетит и вернется), берется разбиение с наибольшей выполнимой
   длиной — невыполнимые куски остаются нераспределенными.

Проверка выполнимости — та же ``_solo_feasible``, что у маршрутизации, так
что после подгонки каждый выполнимый кусок гарантированно кому-то назначится.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner.coverage import split_long_track

from .cluster import Track, Vehicle, _solo_feasible

DEFAULT_MAX_PARTS = 64  # больше частей из одного галса — уже не галс, а «пунктир»
_MIN_PIECE_M = 1.0  # обрезки короче — численный мусор на границе круга связи
_COMM_QUAD_SEGS = 64  # вписанный многоугольник круга лежит внутри круга — оценка консервативна


@dataclass
class FitResult:
    tracks: list[LineString] = field(default_factory=list)
    out_of_comm_range: list[LineString] = field(default_factory=list)


def _flatten_lines(geom: BaseGeometry) -> list[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom] if geom.length >= _MIN_PIECE_M else []
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        result: list[LineString] = []
        for part in geom.geoms:
            result.extend(_flatten_lines(part))
        return result
    return []


def _feasible_for_any(piece: LineString, vehicles: list[Vehicle]) -> bool:
    track = Track(id="", geometry=piece)
    return any(_solo_feasible(v, track) for v in vehicles)


def _split_for_budget(track: LineString, vehicles: list[Vehicle], max_parts: int) -> list[LineString]:
    if _feasible_for_any(track, vehicles):
        return [track]

    best_parts, best_len = [track], 0.0
    for n in range(2, max_parts + 1):
        parts = split_long_track(track, track.length / n)
        feasible_len = sum(p.length for p in parts if _feasible_for_any(p, vehicles))
        if feasible_len >= track.length - 1e-6:
            return parts
        if feasible_len > best_len + 1e-6:
            best_parts, best_len = parts, feasible_len
    return _merge_neighbours(best_parts, vehicles)


def _merge_neighbours(parts: list[LineString], vehicles: list[Vehicle]) -> list[LineString]:
    """Склеивает соседние куски, пока склейка не меняет выполнимость: самое
    мелкое разбиение дает наибольшую выполнимую длину, но оставляет «пунктир»
    из десятков коротких галсов там, где хватило бы нескольких длинных."""
    merged: list[tuple[LineString, bool]] = []
    for part in parts:
        ok = _feasible_for_any(part, vehicles)
        if merged and merged[-1][1] == ok:
            joined = LineString(list(merged[-1][0].coords) + list(part.coords)[1:])
            if not ok or _feasible_for_any(joined, vehicles):
                merged[-1] = (joined, ok)
                continue
        merged.append((part, ok))
    return [part for part, _ in merged]


def fit_tracks_to_vehicles(
    tracks: list[LineString], vehicles: list[Vehicle], max_parts: int = DEFAULT_MAX_PARTS
) -> FitResult:
    """Обрезает галсы по зоне связи бортов и режет на части под энергобюджет
    (см. docstring модуля). Порядок галсов сохраняется."""
    result = FitResult()
    if not vehicles:
        result.tracks = list(tracks)
        return result

    comm_area: BaseGeometry | None = None
    if all(v.comm_range_m is not None for v in vehicles):
        comm_area = unary_union(
            [v.launch_point.buffer(v.comm_range_m, quad_segs=_COMM_QUAD_SEGS) for v in vehicles]
        )

    for track in tracks:
        pieces = [track]
        if comm_area is not None and not comm_area.covers(track):
            pieces = _flatten_lines(track.intersection(comm_area))
            result.out_of_comm_range.extend(_flatten_lines(track.difference(comm_area)))
        for piece in pieces:
            result.tracks.extend(_split_for_budget(piece, vehicles, max_parts))
    return result
