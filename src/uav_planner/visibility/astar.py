"""Обход запрещенных зон на переходах — сеточный A* с последующим спрямлением
пути («string pulling») по точной геометрии препятствий.

Раньше переходы между галсами и до площадки строились прямыми линиями без
учета препятствий (см. известные ограничения модуля «Планирование»,
main/README.md). Этот модуль подменяет прямую линию маршрутом в обход
бесполетных зон и высотных препятствий там, где прямая линия их пересекает —
и только там: если прямая уже свободна, сетка вообще не строится.

Метод: 1) если прямая ``start-goal`` не задевает ни одного препятствия —
возвращается она сама; 2) иначе строится локальная сетка (только вокруг
отрезка ``start-goal`` и мешающих ему препятствий, не по всей сцене) и по ней
запускается A* (8-связность, эвклидова эвристика — допустима и согласована
при диагональной цене sqrt(2)); 3) сеточный путь спрямляется жадным
"string pulling" — каждая пара точек, между которыми существует свободная
прямая видимость (проверяется точной геометрией препятствий, а не сеткой),
соединяется напрямую. Это дает маршрут, огибающий углы препятствий, а не
"лестницу" из сеточных ячеек.

v1-ограничения (честно, а не молча):
  - сетка, а не граф видимости по вершинам препятствий — точность обхода
    ограничена размером ячейки (``cell_size_m``); для очень узких проходов
    между препятствиями путь может не найтись, даже если он существует
    геометрически (тогда возвращается прямая линия с ``fallback=True`` —
    столкновение при этом будет честно поймано независимой проверкой
    безопасности, см. ``uav_planner.safety.check_geozones``);
  - размер сетки ограничен ``MAX_GRID_DIM`` ячеек на сторону — при
    необходимости шаг сетки автоматически укрупняется, чтобы не подвесить
    расчет на очень длинных переходах.

``allowed_space`` (опционально) — union активных на съемочной высоте зон
разрешенного пространства: если задан, область ЗА его пределами (в границах
локальной сетки перехода) добавляется в число препятствий наравне с БПЗ и
высотными препятствиями — раньше переход строил кратчайший обход только
БПЗ/препятствий и мог по прямой уйти за границу разрешенного пространства
(особенно на невыпуклой границе — «залив» на карте разрешенной зоны), если
сама прямая ничего из явных препятствий не задевала. Без этого параметра
поведение прежнее (для существующих вызовов/тестов, которых это не касается).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Sequence

from shapely.geometry import LineString, Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep

DEFAULT_CELL_SIZE_M = 20.0
MAX_GRID_DIM = 200
GRID_PADDING_CELLS = 2
MIN_PADDING_M = 50.0

_SQRT2 = math.sqrt(2)
_NEIGHBORS = (
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, _SQRT2), (1, -1, _SQRT2), (-1, 1, _SQRT2), (-1, -1, _SQRT2),
)

Cell = tuple[int, int]


@dataclass(frozen=True)
class PathResult:
    line: LineString
    detoured: bool  # True — прямая была перекрыта, и найден обходной путь
    fallback: bool  # True — прямая перекрыта, но обход не найден (возвращена все равно прямая)


def _clear(a: tuple[float, float], b: tuple[float, float], merged_obstacle: BaseGeometry) -> bool:
    """Есть ли прямая видимость между точками — отрезок не заходит внутрь препятствий
    (может касаться границы, огибая угол впритык)."""
    if merged_obstacle.is_empty:
        return True
    seg = LineString([a, b])
    return not seg.intersects(merged_obstacle) or seg.touches(merged_obstacle)


def _simplify(points: list[tuple[float, float]], merged_obstacle: BaseGeometry) -> list[tuple[float, float]]:
    """Жадное спрямление (string pulling): пропускает промежуточные сеточные
    точки там, где до более дальней точки есть прямая видимость."""
    if len(points) <= 2:
        return points
    result = [points[0]]
    i = 0
    n = len(points)
    while i < n - 1:
        j = n - 1
        while j > i + 1 and not _clear(points[i], points[j], merged_obstacle):
            j -= 1
        result.append(points[j])
        i = j
    return result


def _heuristic(a: Cell, b: Cell) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _astar_grid(start: Cell, goal: Cell, blocked, nx: int, ny: int) -> list[Cell] | None:
    """Стандартный A* по сетке. ``blocked(i, j)`` — занята ли ячейка;
    ``start``/``goal`` всегда считаются свободными (это заявленные положения
    БВС и площадки/галса — доверяем вызывающему коду, что они в свободном
    пространстве, даже если попали на грань буфера препятствия)."""
    open_heap: list[tuple[float, float, Cell]] = [(_heuristic(start, goal), 0.0, start)]
    came_from: dict[Cell, Cell] = {}
    g_score: dict[Cell, float] = {start: 0.0}
    closed: set[Cell] = set()

    while open_heap:
        _, g, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path
        closed.add(current)

        for dx, dy, cost in _NEIGHBORS:
            nb = (current[0] + dx, current[1] + dy)
            if not (0 <= nb[0] < nx and 0 <= nb[1] < ny):
                continue
            if nb != goal and nb != start and blocked(*nb):
                continue
            if dx != 0 and dy != 0:
                # не срезаем по диагонали через угол занятой ячейки
                ortho_a, ortho_b = (current[0] + dx, current[1]), (current[0], current[1] + dy)
                if (ortho_a != goal and ortho_a != start and blocked(*ortho_a)) or \
                   (ortho_b != goal and ortho_b != start and blocked(*ortho_b)):
                    continue
            tentative = g + cost
            if tentative < g_score.get(nb, math.inf):
                g_score[nb] = tentative
                came_from[nb] = current
                heapq.heappush(open_heap, (tentative + _heuristic(nb, goal), tentative, nb))

    return None


def find_path(
    start: Point,
    goal: Point,
    obstacles: Sequence[BaseGeometry],
    allowed_space: BaseGeometry | None = None,
    cell_size_m: float = DEFAULT_CELL_SIZE_M,
) -> PathResult:
    """Маршрут ``start -> goal``, обходящий ``obstacles`` (бесполетные зоны и
    высотные препятствия — их ``footprint()``) и не выходящий за границу
    ``allowed_space`` (если задана), если прямая линия нарушает то или другое."""
    direct = LineString([start, goal])
    if direct.length == 0:
        return PathResult(direct, False, False)

    padding = max(cell_size_m * GRID_PADDING_CELLS, 0.15 * direct.length, MIN_PADDING_M)
    search_box = box(*direct.buffer(padding).bounds)
    relevant = [o for o in obstacles if o is not None and not o.is_empty and search_box.intersects(o)]

    outside_allowed = None
    if allowed_space is not None and not allowed_space.is_empty:
        # «Препятствие» — всё внутри локальной сетки перехода, что лежит ЗА
        # пределами разрешенного пространства (граница сетки гарантирует, что
        # это всегда конечный полигон, даже если allowed_space сам не выпуклый
        # или имеет дыры). Считается на ``search_box``, а не на итоговых
        # границах сетки — те увеличатся ниже вторым отступом ``padding`` и
        # округлением ``MAX_GRID_DIM``; см. пересчет ниже.
        outside_allowed = search_box.difference(allowed_space)
        if not outside_allowed.is_empty:
            relevant.append(outside_allowed)

    if not relevant:
        return PathResult(direct, False, False)

    merged = unary_union(relevant)
    if merged.is_empty or _clear((start.x, start.y), (goal.x, goal.y), merged):
        return PathResult(direct, False, False)

    minx, miny, maxx, maxy = unary_union([direct, merged]).bounds
    minx, miny, maxx, maxy = minx - padding, miny - padding, maxx + padding, maxy + padding

    cell = cell_size_m
    nx = max(1, math.ceil((maxx - minx) / cell))
    ny = max(1, math.ceil((maxy - miny) / cell))
    if nx > MAX_GRID_DIM or ny > MAX_GRID_DIM:
        scale = max(nx, ny) / MAX_GRID_DIM
        cell *= scale
        nx = max(1, math.ceil((maxx - minx) / cell))
        ny = max(1, math.ceil((maxy - miny) / cell))

    if outside_allowed is not None:
        # Итоговая сетка (после отступа выше и, возможно, укрупнения ячейки
        # под MAX_GRID_DIM) шире, чем ``search_box``, на котором считали
        # outside_allowed — без пересчета клетки в этой кайме молча считались
        # бы свободными, хотя лежат за пределами allowed_space, и найденный
        # «обход» мог реально выходить за границу разрешенного пространства
        # именно там (пойманная живым тестом ошибка, не гипотетическая).
        grid_box = box(minx, miny, maxx, maxy)
        outside_allowed = grid_box.difference(allowed_space)
        relevant = [o for o in obstacles if o is not None and not o.is_empty and grid_box.intersects(o)]
        if not outside_allowed.is_empty:
            relevant.append(outside_allowed)
        merged = unary_union(relevant) if relevant else outside_allowed

    prepared = prep(merged)
    inset = cell * 1e-6  # cell лишь касающийся границы препятствия не считается занятым

    def blocked(i: int, j: int) -> bool:
        cx0, cy0 = minx + i * cell, miny + j * cell
        return prepared.intersects(box(cx0 + inset, cy0 + inset, cx0 + cell - inset, cy0 + cell - inset))

    def to_cell(p: Point) -> Cell:
        i = min(max(int((p.x - minx) / cell), 0), nx - 1)
        j = min(max(int((p.y - miny) / cell), 0), ny - 1)
        return (i, j)

    start_cell, goal_cell = to_cell(start), to_cell(goal)
    grid_path = _astar_grid(start_cell, goal_cell, blocked, nx, ny)
    if grid_path is None:
        return PathResult(direct, False, True)

    def cell_center(c: Cell) -> tuple[float, float]:
        return (minx + (c[0] + 0.5) * cell, miny + (c[1] + 0.5) * cell)

    raw_points = [(start.x, start.y)] + [cell_center(c) for c in grid_path[1:-1]] + [(goal.x, goal.y)]
    simplified = _simplify(raw_points, merged)
    return PathResult(LineString(simplified), True, False)
