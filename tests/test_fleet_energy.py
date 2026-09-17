import pytest

from uav_planner.fleet import FLEET_MODELS, flight_time_budget_s


def test_default_budget_geoscan_201():
    model = FLEET_MODELS["geoscan-201"]
    budget_s = flight_time_budget_s(model)
    assert budget_s == pytest.approx(180 * 60 * 0.8)


def test_maneuver_margin_reduces_budget():
    model = FLEET_MODELS["geoscan-gemini"]
    base = flight_time_budget_s(model)
    reduced = flight_time_budget_s(model, maneuver_margin=0.1)
    assert reduced == pytest.approx(base * 0.9)
    assert reduced < base


def test_rejects_invalid_energy_reserve():
    model = FLEET_MODELS["geoscan-801"]
    with pytest.raises(ValueError):
        flight_time_budget_s(model, energy_reserve=1.0)
    with pytest.raises(ValueError):
        flight_time_budget_s(model, energy_reserve=-0.1)


def test_rejects_invalid_maneuver_margin():
    model = FLEET_MODELS["geoscan-801"]
    with pytest.raises(ValueError):
        flight_time_budget_s(model, maneuver_margin=1.0)
