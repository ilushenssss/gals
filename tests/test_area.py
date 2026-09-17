from datetime import datetime, timedelta

import pytest
from shapely.geometry import Polygon

from uav_planner.geometry import (
    AllowedZone,
    GeometryError,
    HeightRange,
    NoFlyZone,
    Obstacle,
    TimeWindow,
    compute_working_area,
    safe_simplify,
    validate_polygon,
)


def square(x0, y0, size):
    return Polygon([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)])


def test_validate_polygon_accepts_valid():
    poly = square(0, 0, 100)
    assert validate_polygon(poly) is poly


def test_validate_polygon_rejects_self_intersection():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    with pytest.raises(GeometryError):
        validate_polygon(bowtie)


def test_validate_polygon_rejects_empty():
    with pytest.raises(GeometryError):
        validate_polygon(Polygon())


def test_working_area_equals_intersection_with_allowed_when_no_obstacles():
    area = square(0, 0, 1000)
    allowed = [AllowedZone(id="a1", polygon=square(-100, -100, 1200), height=HeightRange(0, 500))]

    working = compute_working_area(area, allowed, [], [], survey_height_m=100.0)

    assert working.equals(area)


def test_working_area_raises_when_height_not_covered():
    area = square(0, 0, 1000)
    allowed = [AllowedZone(id="a1", polygon=square(-100, -100, 1200), height=HeightRange(0, 50))]

    with pytest.raises(GeometryError):
        compute_working_area(area, allowed, [], [], survey_height_m=100.0)


def test_no_fly_zone_is_subtracted_with_buffer():
    area = square(0, 0, 1000)
    allowed = [AllowedZone(id="a1", polygon=square(-100, -100, 1200), height=HeightRange(0, 500))]
    no_fly = [NoFlyZone(id="nf1", polygon=square(400, 400, 100), safety_buffer_m=20.0)]

    working = compute_working_area(area, allowed, no_fly, [], survey_height_m=100.0)

    buffered_no_fly = no_fly[0].footprint()
    assert working.intersection(buffered_no_fly).area == pytest.approx(0.0, abs=1e-6)
    assert working.area < area.area


def test_obstacle_above_survey_height_becomes_hole():
    area = square(0, 0, 1000)
    allowed = [AllowedZone(id="a1", polygon=square(-100, -100, 1200), height=HeightRange(0, 500))]
    tall_obstacle = Obstacle(id="o1", polygon=square(200, 200, 50), height=HeightRange(0, 150))

    working = compute_working_area(area, allowed, [], [tall_obstacle], survey_height_m=100.0)

    assert working.intersection(tall_obstacle.polygon).area == pytest.approx(0.0, abs=1e-6)


def test_obstacle_below_survey_height_is_ignored():
    area = square(0, 0, 1000)
    allowed = [AllowedZone(id="a1", polygon=square(-100, -100, 1200), height=HeightRange(0, 500))]
    low_obstacle = Obstacle(id="o1", polygon=square(200, 200, 50), height=HeightRange(0, 30))

    working = compute_working_area(area, allowed, [], [low_obstacle], survey_height_m=100.0)

    assert working.equals(area)


def test_no_fly_zone_inactive_outside_time_window_is_ignored():
    area = square(0, 0, 1000)
    allowed = [AllowedZone(id="a1", polygon=square(-100, -100, 1200), height=HeightRange(0, 500))]
    window = TimeWindow(datetime(2026, 9, 1), datetime(2026, 9, 2))
    no_fly = [
        NoFlyZone(id="nf1", polygon=square(400, 400, 100), active_windows=[window])
    ]

    outside = datetime(2026, 9, 10)
    working = compute_working_area(area, allowed, no_fly, [], survey_height_m=100.0, when=outside)

    assert working.equals(area)

    inside = datetime(2026, 9, 1, 12, 0)
    working_active = compute_working_area(
        area, allowed, no_fly, [], survey_height_m=100.0, when=inside
    )
    assert working_active.area < area.area


def test_safe_simplify_rejects_tolerance_over_buffer():
    poly = square(0, 0, 1000)
    with pytest.raises(ValueError):
        safe_simplify(poly, tolerance_m=5.0, safety_buffer_m=5.0)


def test_safe_simplify_accepts_smaller_tolerance():
    poly = square(0, 0, 1000)
    simplified = safe_simplify(poly, tolerance_m=1.0, safety_buffer_m=5.0)
    assert simplified.is_valid
    assert simplified.equals(poly) or simplified.area == pytest.approx(poly.area, rel=0.05)
