"""Раздел рабочей области между группами «модель + камера» смешанного парка.

Галсы разных групп несовместимы: у каждой своя высота съемки и шаг галсов
(см. ``camera``), поэтому общий набор галсов между ними не поделить, как
Шаг 2 делит галсы между бортами одной группы. Делится сама область — до
построения галсов, а галсы каждая группа строит уже на своей части.

Как делится:

1. Область раскладывается на ячейки (та же boustrophedon-декомпозиция, что
   строит галсы), и каждая ячейка режется на **полосы вдоль своего
   направления галсов**. Резать поперек нельзя — галсы бы укоротились и
   разворотов стало бы больше; полоса же целиком состоит из полных галсов.
   Ширина полосы подбирается так, чтобы кусков было около
   ``target_chunks`` (достаточно мелко, чтобы поделить нагрузку, и не мельче
   двух шагов галсов самой разреженной группы).
2. Куски раздаются наращиванием территорий: на каждом шаге группа с
   наименьшей оценкой времени берет еще не занятый кусок, ближайший к уже
   своим (первый — ближайший к ее площадкам). Так территории остаются
   связными, а нагрузка выравнивается по времени, а не по площади: у группы
   с широкой полосой захвата и быстрыми бортами кусок «дешевле».

Оценка времени куска для группы — налет по галсам (площадь / (шаг · скорость))
плюс перелеты туда-обратно и замена АКБ на каждый вылет, который этот кусок
потребует; делится на число бортов группы. Это грубая прокси — точное время
считает уже полный расчет каждой группы, а смешанный план соревнуется с
однотипными по тому же критерию J (см. ``plan_service._pick_best_candidate``),
так что неудачный раздел просто проиграет.

Кусок достается только группе, чья рабочая область покрывает его (почти)
целиком: на высоте другой группы там может быть препятствие или нет
разрешенного пространства. Если целиком его не покрывает никто, он отходит
группе с наибольшим перекрытием — непокрытый остаток честно покажет проверка
покрытия.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely import affinity
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner.coverage import boustrophedon_cells

DEFAULT_TARGET_CHUNKS = 60
_FULL_COVER_FRACTION = 0.99
_MIN_CHUNK_AREA_M2 = 1.0
_STRIP_PAD_M = 1.0


@dataclass(frozen=True)
class FleetShare:
    """Группа «модель + камера» как участник раздела области."""

    id: str
    area: BaseGeometry  # рабочая область группы на ее высоте съемки (UTM)
    launch_points: tuple[Point, ...]
    spacing_m: float
    speed_mps: float
    vehicles: int
    budget_s: float
    overhead_s: float


def _polygons(geom: BaseGeometry) -> list[BaseGeometry]:
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [p for g in geom.geoms for p in _polygons(g)]
    return []


def _cell_bands(area: BaseGeometry, min_band_m: float, target_chunks: int) -> list[BaseGeometry]:
    cells = boustrophedon_cells(area)
    rotated = [
        (affinity.rotate(c.polygon, -c.direction_deg, origin=(0, 0)), c.direction_deg) for c in cells
    ]
    total_width = sum(r.bounds[3] - r.bounds[1] for r, _ in rotated)
    band_m = max(min_band_m, total_width / max(target_chunks, 1))

    chunks: list[BaseGeometry] = []
    for poly, direction in rotated:
        minx, miny, maxx, maxy = poly.bounds
        n = max(1, round((maxy - miny) / band_m))
        step = (maxy - miny) / n
        for i in range(n):
            strip = box(minx - _STRIP_PAD_M, miny + i * step, maxx + _STRIP_PAD_M, miny + (i + 1) * step)
            for piece in _polygons(poly.intersection(strip)):
                if piece.area >= _MIN_CHUNK_AREA_M2:
                    chunks.append(affinity.rotate(piece, direction, origin=(0, 0)))
    return chunks


def _chunk_time_s(share: FleetShare, chunk: BaseGeometry) -> float | None:
    """Оценка вклада куска во время группы (с. на один борт); ``None`` —
    кусок группе не по силам (не долететь и вернуться в пределах бюджета)."""
    coverable = chunk.intersection(share.area).area
    if coverable <= 0:
        return None
    transit_m = 2.0 * min(chunk.distance(p) for p in share.launch_points)
    useful_s = share.budget_s - transit_m / share.speed_mps
    if useful_s <= 0:
        return None
    survey_s = coverable / (share.spacing_m * share.speed_mps)
    # Доля вылета, а не целые вылеты: кусок — лишь часть вылета, и округление
    # вверх на каждом из десятков кусков насчитывало по замене АКБ на каждый,
    # занижая производительность моделей с долгим полетом в разы.
    sorties = survey_s / useful_s
    return (survey_s + sorties * (transit_m / share.speed_mps + share.overhead_s)) / share.vehicles


def split_area_between_groups(
    area: BaseGeometry, shares: list[FleetShare], target_chunks: int = DEFAULT_TARGET_CHUNKS
) -> dict[str, BaseGeometry]:
    """Делит ``area`` (UTM) между группами (см. docstring модуля). Возвращает
    часть области каждой группы — уже в пределах ее рабочей области; группа
    без единого куска в результат не попадает."""
    if area.is_empty or not shares:
        return {}
    if len(shares) == 1:
        only = shares[0]
        return {only.id: area.intersection(only.area)}

    chunks = _cell_bands(area, 2.0 * max(s.spacing_m for s in shares), target_chunks)

    # Кто может взять каждый кусок и во что это ему обойдется.
    cost: list[dict[str, float]] = []
    for chunk in chunks:
        fractions = {s.id: chunk.intersection(s.area).area / chunk.area for s in shares}
        full = [s for s in shares if fractions[s.id] >= _FULL_COVER_FRACTION]
        allowed = full or [max(shares, key=lambda s: fractions[s.id])]
        options = {}
        for s in allowed:
            t = _chunk_time_s(s, chunk)
            if t is not None:
                options[s.id] = t
        cost.append(options)

    by_id = {s.id: s for s in shares}
    load = {s.id: 0.0 for s in shares}
    owned: dict[str, list[BaseGeometry]] = {s.id: [] for s in shares}
    region: dict[str, BaseGeometry | None] = {s.id: None for s in shares}
    free = {i for i, options in enumerate(cost) if options}

    while free:
        active = [sid for sid in load if any(sid in cost[i] for i in free)]
        sid = min(active, key=lambda k: load[k])
        share = by_id[sid]

        def proximity(i: int) -> tuple[float, float]:
            to_launch = min(chunks[i].distance(p) for p in share.launch_points)
            to_region = chunks[i].distance(region[sid]) if region[sid] is not None else to_launch
            return to_region, to_launch

        pick = min((i for i in free if sid in cost[i]), key=proximity)
        free.remove(pick)
        owned[sid].append(chunks[pick])
        region[sid] = chunks[pick] if region[sid] is None else region[sid].union(chunks[pick])
        load[sid] += cost[pick][sid]

    return {
        sid: unary_union(parts).intersection(by_id[sid].area)
        for sid, parts in owned.items() if parts
    }
