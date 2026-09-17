import pytest

from uav_planner.fleet import FLEET_MODELS, READINESS_STATUSES


def test_three_models_present():
    assert set(FLEET_MODELS.keys()) == {"geoscan-201", "geoscan-gemini", "geoscan-801"}


def test_geoscan_201_matches_concept_table():
    m = FLEET_MODELS["geoscan-201"]
    assert m.uav_type == "самолет"
    assert m.speed_ms.min_ms == pytest.approx(17.8)
    assert m.speed_ms.max_ms == pytest.approx(36.1)
    assert m.max_flight_time_min == pytest.approx(180.0)
    assert m.max_route_km == pytest.approx(210.0)
    assert m.max_wind_ms == pytest.approx(12.0)
    assert m.comm_range_km == pytest.approx(40.0)
    assert m.height_min_m == pytest.approx(100.0)
    assert m.height_max_m is None
    assert "rx1rm2" in m.compatible_cameras and "pollux" in m.compatible_cameras


def test_geoscan_gemini_matches_concept_table():
    m = FLEET_MODELS["geoscan-gemini"]
    assert m.uav_type == "квадрокоптер"
    assert m.max_flight_time_min == pytest.approx(40.0)
    assert m.max_route_km is None
    assert m.max_wind_ms == pytest.approx(10.0)
    assert m.comm_range_km == pytest.approx(5.0)
    assert m.height_min_m == pytest.approx(0.0)
    assert m.height_max_m == pytest.approx(500.0)
    assert "pf1b" in m.compatible_cameras and "pollux" in m.compatible_cameras


def test_geoscan_801_matches_concept_table():
    m = FLEET_MODELS["geoscan-801"]
    assert m.uav_type == "квадрокоптер"
    assert m.max_route_km == pytest.approx(30.0)
    assert m.max_wind_ms == pytest.approx(12.0)
    assert m.comm_range_km == pytest.approx(10.0)
    assert m.height_min_m == pytest.approx(50.0)
    assert m.height_max_m == pytest.approx(500.0)
    assert "thermal640" in m.compatible_cameras and "cam12mp" in m.compatible_cameras


def test_readiness_statuses():
    assert READINESS_STATUSES == ("Готов", "Недоступен", "На обслуживании")
