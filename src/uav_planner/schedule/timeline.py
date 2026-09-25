"""Расписание вылетов: назначение времени старта/посадки по результату
``routing`` с учетом светового дня и накладных расходов между вылетами
(замена АКБ/подготовка — принятое допущение ≈15 мин, см. «Открытые
неточности» плана реализации). См. Математическая_модель.md, раздел 14.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from uav_planner.routing.cluster import Sortie

from .daylight import work_window_utc

DEFAULT_OVERHEAD_S = 15 * 60.0
DEFAULT_MAX_DAYS = 400  # защита от бесконечного цикла (полярная ночь длится не вечно)
# Интервал между взлетами разных бортов с одной площадки. Раньше все борта
# площадки стартовали в одну и ту же секунду начала окна и оказывались в одной
# точке одновременно — проверка «Разведение» честно ловила сближение до 0 м.
# Минута — типичный темп выпуска с одной площадки (подготовка к старту
# следующего борта), за нее первый уходит на сотни метров.
DEFAULT_LAUNCH_INTERVAL_S = 60.0


class ScheduleError(ValueError):
    """Вылет физически не укладывается ни в один световой день в разумном
    горизонте — координаты/дата/длительность вылета противоречат друг другу."""


@dataclass(frozen=True)
class ScheduledSortie:
    sortie: Sortie
    start_utc: datetime
    end_utc: datetime


def assign_timestamps(
    sorties: list[Sortie],
    lat: float,
    lon: float,
    start_date: date,
    window_start: time | None = None,
    window_end: time | None = None,
    tz: tzinfo | None = None,
    overhead_s: float = DEFAULT_OVERHEAD_S,
    max_days: int = DEFAULT_MAX_DAYS,
    start_offset_s: float = 0.0,
) -> list[ScheduledSortie]:
    """Раскладывает вылеты одного БВС во времени начиная с местного дня
    ``start_date``, в границах пересечения окна оператора (местное время
    ``tz``) и светового дня (см. ``work_window_utc``); при выходе за границы
    текущего дня — переход на следующий подходящий день.

    ``start_offset_s`` — задержка первого взлета борта относительно начала
    окна: так разводятся по времени борта одной площадки (см.
    ``DEFAULT_LAUNCH_INTERVAL_S``)."""
    scheduled: list[ScheduledSortie] = []
    day = start_date
    cursor: datetime | None = None
    days_advanced = 0
    pending_offset_s = start_offset_s

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
            window = work_window_utc(lat, lon, day, window_start, window_end, tz)
            if window is None:
                advance_day()
                continue

            win_start, win_end = window
            if cursor is None or cursor < win_start:
                cursor = win_start + timedelta(seconds=pending_offset_s)

            finish = cursor + timedelta(seconds=sortie.flight_time_s)
            if finish <= win_end:
                scheduled.append(ScheduledSortie(sortie=sortie, start_utc=cursor, end_utc=finish))
                cursor = finish + timedelta(seconds=overhead_s)
                pending_offset_s = 0.0
                break

            advance_day()

    return scheduled
