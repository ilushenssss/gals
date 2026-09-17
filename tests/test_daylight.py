from datetime import date

import pytest

from uav_planner.schedule import daylight_window_utc_hours


def test_equinox_day_length_close_to_twelve_hours_anywhere():
    # Около равноденствия (20 марта) продолжительность дня близка к 12ч почти
    # на любой широте (приближенная модель не учитывает атмосферную рефракцию).
    sunrise, sunset = daylight_window_utc_hours(55.75, 37.60, date(2026, 3, 20))
    assert (sunset - sunrise) == pytest.approx(12.0, abs=0.3)


def test_moscow_summer_solstice_has_long_day():
    sunrise, sunset = daylight_window_utc_hours(55.75, 37.60, date(2026, 6, 21))
    assert (sunset - sunrise) > 16.0


def test_moscow_winter_solstice_has_short_day():
    sunrise, sunset = daylight_window_utc_hours(55.75, 37.60, date(2026, 12, 21))
    assert (sunset - sunrise) < 8.0


def test_equator_day_length_is_always_close_to_twelve_hours():
    for month_day in [(3, 20), (6, 21), (12, 21)]:
        sunrise, sunset = daylight_window_utc_hours(0.0, 0.0, date(2026, *month_day))
        assert (sunset - sunrise) == pytest.approx(12.0, abs=0.2)


def test_longitude_shifts_solar_noon():
    # Восток (Владивосток, ~131 в.д.) -> восход раньше по UTC, чем в Москве.
    msk_sunrise, _ = daylight_window_utc_hours(55.75, 37.60, date(2026, 3, 20))
    vvo_sunrise, _ = daylight_window_utc_hours(43.1, 131.9, date(2026, 3, 20))
    assert vvo_sunrise < msk_sunrise


def test_polar_night_returns_none():
    assert daylight_window_utc_hours(78.0, 15.0, date(2026, 12, 21)) is None


def test_polar_day_returns_full_day():
    assert daylight_window_utc_hours(78.0, 15.0, date(2026, 6, 21)) == (0.0, 24.0)
