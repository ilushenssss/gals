import pytest
from shapely.geometry import Polygon, box

from uav_planner.coverage import best_sweep_direction, crosswind_fraction


def test_best_direction_along_long_side_of_rectangle():
    # Прямоугольник 100 (x) x 40 (y): галсы вдоль длинной стороны (x) дают
    # меньшую ширину поперек (40) и значит меньше разворотов, чем поперек (100).
    rect = box(0, 0, 100, 40)
    direction = best_sweep_direction(rect)
    assert direction == pytest.approx(0.0, abs=1e-6)


def test_best_direction_rotated_rectangle():
    # Тот же прямоугольник, повернутый на 37° — направление галсов должно
    # повернуться вместе с ним (а не остаться на коротком борте под 127°).
    rect = box(0, 0, 100, 40)
    from shapely import affinity

    rotated = affinity.rotate(rect, 37.0, origin=(0, 0))
    direction = best_sweep_direction(rotated)
    assert direction == pytest.approx(37.0, abs=1e-6)


def test_wind_penalty_can_flip_choice_between_close_widths():
    # 100 (x) x 95 (y): без ветра чуть выгоднее направление 0 (ширина 95 < 100).
    rect = box(0, 0, 100, 95)

    no_wind = best_sweep_direction(rect)
    assert no_wind == pytest.approx(0.0, abs=1e-6)

    # Ветер строго вдоль оси Y (перпендикулярно направлению 0) - штраф default 0.15
    # достаточен, чтобы сделать направление 90 (галсы вдоль ветра) выгоднее.
    with_wind = best_sweep_direction(rect, wind_bearing_deg=90.0)
    assert with_wind == pytest.approx(90.0, abs=1e-6)


def test_crosswind_fraction_bounds():
    assert crosswind_fraction(0.0, 0.0) == pytest.approx(0.0)
    assert crosswind_fraction(0.0, 90.0) == pytest.approx(1.0)
    assert crosswind_fraction(45.0, 0.0) == pytest.approx(math_sin_45())


def math_sin_45():
    import math

    return math.sin(math.radians(45.0))


def test_degenerate_geometry_returns_zero():
    point = Polygon()  # пустой полигон
    assert best_sweep_direction(point) == 0.0
