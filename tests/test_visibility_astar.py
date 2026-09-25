"""Тесты обхода препятствий сеточным A* — uav_planner.visibility.astar."""

import math
import random

from shapely.geometry import LineString, Point, Polygon, box

from uav_planner.visibility import find_path


def test_direct_line_used_when_no_obstacle():
    start, goal = Point(0, 0), Point(100, 0)
    result = find_path(start, goal, [])
    assert result.detoured is False
    assert result.fallback is False
    assert list(result.line.coords) == [(0, 0), (100, 0)]


def test_direct_line_used_when_obstacle_far_away():
    start, goal = Point(0, 0), Point(100, 0)
    far_obstacle = box(1000, 1000, 1010, 1010)
    result = find_path(start, goal, [far_obstacle])
    assert result.detoured is False
    assert result.fallback is False


def test_path_detours_around_blocking_obstacle():
    start, goal = Point(0, 0), Point(200, 0)
    # Прямоугольник перекрывает прямую линию по всей ширине маршрута.
    obstacle = box(80, -50, 120, 50)
    result = find_path(start, goal, [obstacle], cell_size_m=10.0)

    assert result.fallback is False
    assert result.detoured is True
    # Маршрут не должен заходить внутрь препятствия (граница/касание допустимы).
    assert not result.line.intersects(obstacle) or result.line.touches(obstacle)
    # Начинается и кончается в заданных точках.
    coords = list(result.line.coords)
    assert coords[0] == (0.0, 0.0)
    assert coords[-1] == (200.0, 0.0)
    # Действительно дальше прямой (потребовался обход).
    assert result.line.length > LineString([start, goal]).length


def test_path_is_simplified_not_a_staircase():
    start, goal = Point(0, 0), Point(200, 0)
    obstacle = box(80, -50, 120, 50)
    result = find_path(start, goal, [obstacle], cell_size_m=5.0)
    # Спрямление должно давать компактный путь, а не десятки сеточных точек.
    assert len(result.line.coords) <= 6


def test_fallback_when_goal_fully_enclosed():
    start = Point(-100, 0)
    goal = Point(0, 0)
    # Кольцо препятствий вокруг цели со всех сторон — обхода внутри
    # локальной сетки не существует.
    ring = [
        box(-20, -20, 20, -10),
        box(-20, 10, 20, 20),
        box(-20, -20, -10, 20),
        box(10, -20, 20, 20),
    ]
    result = find_path(start, goal, ring, cell_size_m=5.0)
    assert result.fallback is True
    assert result.detoured is False
    assert list(result.line.coords) == [(-100.0, 0.0), (0.0, 0.0)]


def test_start_and_goal_cells_are_never_blocked():
    # Старт стоит вплотную к препятствию (типичный случай площадки у границы
    # буфера БПЗ) — путь все равно должен быть найден, а не отвергнут как
    # «стартуем из занятой клетки».
    start = Point(5, 0)
    goal = Point(200, 0)
    obstacle = box(0, -50, 15, 50).union(box(80, -50, 120, 50))
    result = find_path(start, goal, [obstacle], cell_size_m=10.0)
    assert result.fallback is False


# ---------- allowed_space: переход не должен выходить за границу разрешенного пространства ----------

def test_direct_line_used_when_it_stays_inside_allowed_space():
    start, goal = Point(0, 0), Point(100, 0)
    allowed = box(-50, -50, 200, 50)
    result = find_path(start, goal, [], allowed_space=allowed)
    assert result.detoured is False
    assert result.fallback is False
    assert list(result.line.coords) == [(0, 0), (100, 0)]


def test_path_detours_to_stay_inside_a_concave_allowed_space():
    # Разрешенное пространство — «буква П»: два прохода вдоль краёв и
    # прорезь посередине, где напрямую пролетать нельзя. Старт и цель по
    # разные стороны прорези — прямая линия неизбежно пересекает недозволенную
    # область снаружи «П», хотя явных препятствий (БПЗ/высотных) вовсе нет.
    allowed = box(-50, -50, 400, 50).difference(box(150, -50, 250, 20))
    start, goal = Point(0, 0), Point(400, 0)
    result = find_path(start, goal, [], allowed_space=allowed, cell_size_m=10.0)

    assert result.fallback is False
    assert result.detoured is True
    # Весь маршрут (за исключением численной погрешности на самой границе)
    # должен лежать внутри разрешенного пространства.
    assert result.line.buffer(1e-6).within(allowed.buffer(1.0))


def test_no_allowed_space_means_old_behavior_unchanged():
    start, goal = Point(0, 0), Point(100, 0)
    result = find_path(start, goal, [])
    assert result.detoured is False
    assert result.fallback is False


def _star_polygon(cx, cy, r_outer, r_inner, n_points, rotation=0.0):
    coords = []
    for i in range(n_points * 2):
        angle = rotation + math.pi * i / n_points
        r = r_outer if i % 2 == 0 else r_inner
        coords.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    coords.append(coords[0])
    return Polygon(coords)


def test_detour_never_reports_success_while_leaking_outside_allowed_space():
    # Регрессия на реальную найденную ошибку: границы локальной сетки A*
    # расширяются вторым отступом ``padding`` (и, при MAX_GRID_DIM,
    # укрупнением ячейки) ПОСЛЕ того, как посчитан «за пределами
    # allowed_space» полигон — тот считался на исходном, более узком
    # search_box. В кайме между старой и новой границей клетки молча
    # считались свободными, хотя реально снаружи allowed_space — обход
    # мог заявить успех (detoured=True), но часть маршрута всё равно
    # оставалась за пределами разрешённого пространства. Звезда с глубокими
    # вырезами между лучами (как в docs/Образцы_данных/
    # obstanovka_slozhnaya_geometria.geojson) надёжно эту кайму задевает.
    star = _star_polygon(0, 0, r_outer=7000, r_inner=4200, n_points=12)
    minx, miny, maxx, maxy = star.bounds

    random.seed(42)
    inside_points = []
    while len(inside_points) < 60:
        p = Point(random.uniform(minx, maxx), random.uniform(miny, maxy))
        if star.contains(p):
            inside_points.append(p)

    checked_detours = 0
    for _ in range(40):
        a, b = random.sample(inside_points, 2)
        result = find_path(a, b, [], allowed_space=star)
        stays_inside = result.line.within(star.buffer(1.0))
        if result.detoured:
            checked_detours += 1
            assert stays_inside, (
                f"detoured=True, но маршрут выходит за пределы allowed_space: {a} -> {b}"
            )
        elif not result.fallback:
            # Ни обход не потребовался, ни он не провалился — значит прямая
            # линия сама была объявлена решением, и она обязана быть внутри.
            assert stays_inside, f"прямая линия принята как есть, но выходит за пределы: {a} -> {b}"
    assert checked_detours > 0, "тест не проверил ни одного реального обхода — сценарий ослаб"
