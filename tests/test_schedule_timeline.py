from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

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
        window_start=time(10), window_end=time(12), overhead_s=0,
    )
    assert scheduled[0].start_utc.date() == date(2026, 3, 20)
    assert scheduled[1].start_utc.date() > date(2026, 3, 20)


def test_respects_operator_window_bounds():
    sorties = [sortie_with_flight_time(1800)]
    scheduled = assign_timestamps(
        sorties, 55.75, 37.60, date(2026, 6, 21),
        window_start=time(10), window_end=time(11),
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


def test_operator_window_is_local_time_of_task_timezone():
    # Регрессия: окно «09:00–18:00» понималось как UTC, а интерфейс
    # показывает время в местном поясе — в Москве это было 12:00–21:00 МСК.
    msk = ZoneInfo("Europe/Moscow")
    scheduled = assign_timestamps(
        [sortie_with_flight_time(1800)], 55.75, 37.60, date(2026, 6, 21),
        window_start=time(9), window_end=time(18), tz=msk,
    )
    assert scheduled[0].start_utc == datetime(2026, 6, 21, 6, 0, tzinfo=timezone.utc)
    assert scheduled[0].start_utc.astimezone(msk).hour == 9


def test_eastern_longitude_keeps_morning_daylight():
    # Регрессия: Владивосток, 21 июня — восход около −4.4 ч UTC. Раньше окно
    # обрезалось по UTC-суткам [0, 24] и утро (≈05:36–10:00 местного)
    # терялось; теперь первый вылет — сразу после восхода.
    vvo = ZoneInfo("Asia/Vladivostok")
    scheduled = assign_timestamps(
        [sortie_with_flight_time(1800)], 43.1, 131.9, date(2026, 6, 21), tz=vvo,
    )
    local_start = scheduled[0].start_utc.astimezone(vvo)
    assert local_start.date() == date(2026, 6, 21)
    assert 5 <= local_start.hour < 6
    assert scheduled[0].start_utc.date() == date(2026, 6, 20)  # в UTC это ещё предыдущие сутки


def test_start_offset_delays_only_first_sortie():
    base = assign_timestamps([sortie_with_flight_time(600)], 55.75, 37.60, date(2026, 3, 20))
    shifted = assign_timestamps(
        [sortie_with_flight_time(600), sortie_with_flight_time(600)], 55.75, 37.60, date(2026, 3, 20),
        overhead_s=900, start_offset_s=60,
    )
    assert (shifted[0].start_utc - base[0].start_utc).total_seconds() == pytest.approx(60)
    assert (shifted[1].start_utc - shifted[0].end_utc).total_seconds() == pytest.approx(900)


def test_polar_night_skips_days_without_daylight():
    # 78° с. ш., 21 декабря — полярная ночь: дней с окном нет, и через
    # max_days расписание честно сдается, а не зацикливается.
    with pytest.raises(ScheduleError):
        assign_timestamps([sortie_with_flight_time(600)], 78.0, 15.0, date(2026, 12, 21), max_days=3)
