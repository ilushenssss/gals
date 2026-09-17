from datetime import date

import pytest

from uav_planner.routing import Sortie, Track
from uav_planner.schedule import ScheduleError, assign_timestamps


def sortie_with_flight_time(seconds):
    s = Sortie(vehicle_id="A")
    s.flight_time_s = seconds
    return s


def test_single_short_sortie_scheduled_within_day():
    # Равноденствие (20 марта), Москва: восход/закат ~UTC 3.6/15.6ч.
    sorties = [sortie_with_flight_time(3600)]  # 1 час
    scheduled = assign_timestamps(sorties, 55.75, 37.60, date(2026, 3, 20))
    assert len(scheduled) == 1
    assert scheduled[0].start_utc.date() == date(2026, 3, 20)
    assert (scheduled[0].end_utc - scheduled[0].start_utc).total_seconds() == pytest.approx(3600)


def test_second_sortie_starts_after_overhead():
    sorties = [sortie_with_flight_time(3600), sortie_with_flight_time(1800)]
    scheduled = assign_timestamps(sorties, 55.75, 37.60, date(2026, 3, 20), overhead_s=900)
    gap = (scheduled[1].start_utc - scheduled[0].end_utc).total_seconds()
    assert gap == pytest.approx(900)


def test_sortie_deferred_to_next_day_when_window_too_short_remaining():
    # Окно оператора искусственно узкое (2 часа), первый вылет занимает почти
    # все окно -> второй сдвигается на следующий день.
    sorties = [sortie_with_flight_time(6000), sortie_with_flight_time(6000)]
    scheduled = assign_timestamps(
        sorties, 55.75, 37.60, date(2026, 3, 20),
        window_start_hour=10.0, window_end_hour=12.0, overhead_s=0,
    )
    assert scheduled[0].start_utc.date() == date(2026, 3, 20)
    assert scheduled[1].start_utc.date() > date(2026, 3, 20)


def test_respects_operator_window_bounds():
    sorties = [sortie_with_flight_time(1800)]
    scheduled = assign_timestamps(
        sorties, 55.75, 37.60, date(2026, 6, 21),
        window_start_hour=10.0, window_end_hour=11.0,
    )
    assert scheduled[0].start_utc.hour == 10


def test_impossible_sortie_raises_schedule_error():
    # Вылет длиннее самого длинного возможного светового дня -> ScheduleError,
    # а не бесконечный цикл (max_days ограничивает горизонт).
    sorties = [sortie_with_flight_time(100 * 3600)]
    with pytest.raises(ScheduleError):
        assign_timestamps(sorties, 55.75, 37.60, date(2026, 6, 21), max_days=5)


def test_empty_sorties_returns_empty_list():
    assert assign_timestamps([], 55.75, 37.60, date(2026, 6, 21)) == []
