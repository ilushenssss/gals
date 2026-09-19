"""Тесты обхода препятствий сеточным A* — uav_planner.visibility.astar."""

from shapely.geometry import LineString, Point, box

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
