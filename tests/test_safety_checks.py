from datetime import date, datetime, timedelta, timezone

import pytest
from shapely.geometry import LineString, Point, box

from uav_planner.safety import (
    SortieTrack,
    check_allowed_space,
    check_coverage,
    check_daylight,
    check_energy,
    check_geozones,
    check_max_altitude,
    check_reachability,
    check_separation,
    discretize,
)


# ---------- discretize ----------

def test_discretize_includes_both_endpoints():
    line = LineString([(0, 0), (100, 0)])
    points = discretize(line, step_m=30)
    assert points[0].coords[0] == (0, 0)
    assert points[-1].coords[0] == (100, 0)


def test_discretize_degenerate_point_line():
    line = LineString([(5, 5), (5, 5)])
    points = discretize(line)
    assert len(points) == 1


# ---------- check_geozones ----------

def test_geozones_passes_when_clear():
    route = LineString([(0, 0), (100, 0)])
    result = check_geozones(route, no_fly_footprints=[box(200, 200, 300, 300)], obstacle_footprints=[])
    assert result.passed


def test_geozones_fails_when_crossing_no_fly():
    route = LineString([(0, 0), (100, 0)])
    no_fly = box(40, -10, 60, 10)
    result = check_geozones(route, no_fly_footprints=[no_fly], obstacle_footprints=[])
    assert not result.passed
    assert "бесполетную зону" in result.violations[0].message
    assert result.violations[0].point is not None


def test_geozones_fails_when_crossing_obstacle():
    route = LineString([(0, 0), (100, 0)])
    obstacle = box(40, -10, 60, 10)
    result = check_geozones(route, no_fly_footprints=[], obstacle_footprints=[obstacle])
    assert not result.passed
    assert "препятствие" in result.violations[0].message


# ---------- check_allowed_space ----------

def test_allowed_space_passes_when_inside():
    route = LineString([(10, 10), (90, 90)])
    allowed = box(0, 0, 100, 100)
    result = check_allowed_space(route, allowed)
    assert result.passed


def test_allowed_space_fails_when_leaving_zone():
    route = LineString([(10, 10), (200, 200)])
    allowed = box(0, 0, 100, 100)
    result = check_allowed_space(route, allowed)
    assert not result.passed


# ---------- check_energy ----------

def test_max_altitude_passes_at_or_below_limit():
    assert check_max_altitude(150.0).passed
    assert check_max_altitude(100.0).passed


def test_max_altitude_fails_above_limit():
    result = check_max_altitude(180.0)
    assert not result.passed
    assert "180" in result.violations[0].message
    assert "150" in result.violations[0].message
    assert result.violations[0].point is None  # высота не привязана к точке маршрута


def test_max_altitude_respects_custom_limit():
    assert not check_max_altitude(120.0, limit_m=100.0).passed
    assert check_max_altitude(80.0, limit_m=100.0).passed


def test_energy_passes_within_budget():
    result = check_energy(route_length_m=1000, cruise_speed_mps=10, budget_s=200)
    assert result.passed


def test_energy_fails_when_transit_pushes_over_budget():
    # 1000м за 10м/с = 100с ровно бюджет: 100с бюджета -> ровно проходит.
    ok = check_energy(route_length_m=1000, cruise_speed_mps=10, budget_s=100)
    assert ok.passed
    # добавляем "переходы", увеличивающие маршрут -> не проходит.
    bad = check_energy(route_length_m=1500, cruise_speed_mps=10, budget_s=100)
    assert not bad.passed
    assert "мин" in bad.violations[0].message
    assert bad.violations[0].point is None  # без переданного route точку не строим

    route = LineString([(0, 0), (1500, 0)])
    bad_with_route = check_energy(route_length_m=1500, cruise_speed_mps=10, budget_s=100, route=route)
    assert bad_with_route.violations[0].point == Point(1000, 0)  # 10 м/с * 100с бюджета


# ---------- check_reachability ----------

def test_reachability_passes_with_nearby_landing_site():
    route = LineString([(0, 0), (100, 0)])
    result = check_reachability(route, [Point(100, 0)], cruise_speed_mps=10, budget_s=1000)
    assert result.passed


def test_reachability_fails_with_no_landing_sites():
    route = LineString([(0, 0), (100, 0)])
    result = check_reachability(route, [], cruise_speed_mps=10, budget_s=1000)
    assert not result.passed


def test_reachability_fails_when_far_point_exceeds_remaining_budget():
    route = LineString([(0, 0), (1000, 0)])
    # Бюджет ровно хватает пролететь маршрут, но не с запасом долета обратно.
    result = check_reachability(route, [Point(0, 0)], cruise_speed_mps=10, budget_s=100)
    assert not result.passed


# ---------- check_coverage ----------

def test_coverage_passes_when_fully_covered():
    working_area = box(0, 0, 100, 20)
    tracks = [LineString([(0, 5), (100, 5)]), LineString([(0, 15), (100, 15)])]
    result = check_coverage(tracks, working_area, swath_m=12)
    assert result.passed


def test_coverage_fails_when_gap_left():
    working_area = box(0, 0, 100, 100)
    tracks = [LineString([(0, 5), (100, 5)])]  # покрывает только узкую полосу
    result = check_coverage(tracks, working_area, swath_m=10)
    assert not result.passed
    assert "не покрыто" in result.violations[0].message
    assert result.violations[0].point is not None


def test_coverage_passes_for_empty_working_area():
    from shapely.geometry import Polygon
    result = check_coverage([], Polygon(), swath_m=10)
    assert result.passed


# ---------- check_daylight ----------

def test_daylight_passes_within_window():
    start = datetime(2026, 6, 21, 6, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    result = check_daylight(start, end, lat=55.75, lon=37.60)
    assert result.passed


def test_daylight_fails_outside_window():
    start = datetime(2026, 12, 21, 22, 0, tzinfo=timezone.utc)  # глубокая ночь зимой в Москве
    end = start + timedelta(hours=1)
    result = check_daylight(start, end, lat=55.75, lon=37.60)
    assert not result.passed


def test_daylight_fails_on_polar_night():
    start = datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    result = check_daylight(start, end, lat=78.0, lon=15.0)
    assert not result.passed
    assert "полярная ночь" in result.violations[0].message


# ---------- check_separation ----------

def test_separation_passes_when_far_apart():
    t0 = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
    sorties = [
        SortieTrack("A", LineString([(0, 0), (100, 0)]), t0, t0 + timedelta(seconds=10), cruise_speed_mps=10),
        SortieTrack("B", LineString([(0, 1000), (100, 1000)]), t0, t0 + timedelta(seconds=10), cruise_speed_mps=10),
    ]
    result = check_separation(sorties, min_separation_m=50)
    assert result.passed


def test_separation_fails_when_close_and_overlapping_in_time():
    t0 = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
    sorties = [
        SortieTrack("A", LineString([(0, 0), (100, 0)]), t0, t0 + timedelta(seconds=10), cruise_speed_mps=10),
        SortieTrack("B", LineString([(0, 5), (100, 5)]), t0, t0 + timedelta(seconds=10), cruise_speed_mps=10),
    ]
    result = check_separation(sorties, min_separation_m=50)
    assert not result.passed
    assert "A" in result.violations[0].message and "B" in result.violations[0].message
    assert result.violations[0].point is not None


def test_separation_passes_when_not_overlapping_in_time():
    t0 = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
    sorties = [
        SortieTrack("A", LineString([(0, 0), (100, 0)]), t0, t0 + timedelta(seconds=10), cruise_speed_mps=10),
        SortieTrack("B", LineString([(0, 5), (100, 5)]), t0 + timedelta(hours=2), t0 + timedelta(hours=2, seconds=10), cruise_speed_mps=10),
    ]
    result = check_separation(sorties, min_separation_m=50)
    assert result.passed


def test_separation_ignores_same_vehicle_pairs():
    t0 = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
    sorties = [
        SortieTrack("A", LineString([(0, 0), (100, 0)]), t0, t0 + timedelta(seconds=10), cruise_speed_mps=10),
        SortieTrack("A", LineString([(0, 5), (100, 5)]), t0, t0 + timedelta(seconds=10), cruise_speed_mps=10),
    ]
    result = check_separation(sorties, min_separation_m=50)
    assert result.passed
