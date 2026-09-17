"""Расписание вылетов: назначение времени старта/посадки по результату
``routing`` с учетом светового дня и накладных расходов между вылетами
(замена АКБ/подготовка — принятое допущение ≈15 мин, см. «Открытые
неточности» плана реализации). См. Математическая_модель.md, раздел 14.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from uav_planner.routing.greedy import Sortie

from .daylight import daylight_window_utc_hours

DEFAULT_OVERHEAD_S = 15 * 60.0
DEFAULT_MAX_DAYS = 400  # защита от бесконечного цикла (полярная ночь длится не вечно)


class ScheduleError(ValueError):
    """Вылет физически не укладывается ни в один световой день в разумном
    горизонте — координаты/дата/длительность вылета противоречат друг другу."""


@dataclass(frozen=True)
class ScheduledSortie:
    sortie: Sortie
    start_utc: datetime
    end_utc: datetime


def _day_window(
    lat: float, lon: float, day: date, window_start_hour: float, window_end_hour: float
) -> tuple[datetime, datetime] | None:
    daylight = daylight_window_utc_hours(lat, lon, day)
    if daylight is None:
        return None
    start_h = max(window_start_hour, daylight[0])
    end_h = min(window_end_hour, daylight[1])
    if start_h >= end_h:
        return None
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return base + timedelta(hours=start_h), base + timedelta(hours=end_h)


def assign_timestamps(
    sorties: list[Sortie],
    lat: float,
    lon: float,
    start_date: date,
    window_start_hour: float = 0.0,
    window_end_hour: float = 24.0,
    overhead_s: float = DEFAULT_OVERHEAD_S,
    max_days: int = DEFAULT_MAX_DAYS,
) -> list[ScheduledSortie]:
    """Раскладывает вылеты одного БВС во времени начиная с ``start_date``, в
    границах пересечения окна оператора и светового дня; при выходе за
    границы текущего дня — переход на следующий подходящий день."""
    scheduled: list[ScheduledSortie] = []
    day = start_date
    cursor: datetime | None = None
    days_advanced = 0

    def advance_day() -> None:
        nonlocal day, cursor, days_advanced
        day += timedelta(days=1)
        cursor = None
        days_advanced += 1
        if days_advanced > max_days:
            raise ScheduleError(
                "не удается подобрать световой день для вылета в разумном горизонте "
                f"({max_days} дней) — проверьте координаты, дату и длительность вылета"
            )

    for sortie in sorties:
        while True:
            window = _day_window(lat, lon, day, window_start_hour, window_end_hour)
            if window is None:
                advance_day()
                continue

            win_start, win_end = window
            if cursor is None or cursor < win_start:
                cursor = win_start

            finish = cursor + timedelta(seconds=sortie.flight_time_s)
            if finish <= win_end:
                scheduled.append(ScheduledSortie(sortie=sortie, start_utc=cursor, end_utc=finish))
                cursor = finish + timedelta(seconds=overhead_s)
                break

            advance_day()

    return scheduled
