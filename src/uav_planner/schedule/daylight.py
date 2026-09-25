"""Восход/закат по приближенным астрономическим формулам.

См. docs/trebovania/Математическая_модель.md, раздел 14. Модель не учитывает
уравнение времени (±16 мин) и относится к местному солнечному времени,
скорректированному только по долготе (без часовых зон) — этого достаточно,
чтобы проверить, пересекается ли окно работ со световым днем (ЗАД.ФТ.3).

Окно работ оператора задается в местном времени задачи (``Task.timezone``),
а световой день считается в UTC. Свести их вместе можно только в абсолютном
времени — это делает ``work_window_utc``, единственное место такого
пересечения для расписания, проверки безопасности и предупреждения задачи.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone, tzinfo


def solar_declination_deg(day_of_year: int) -> float:
    """Склонение Солнца (приближенная формула Купера), градусы."""
    return 23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0))


def daylight_window_utc_hours(lat: float, lon: float, on_date: date) -> tuple[float, float] | None:
    """Восход и закат в часах UTC (0..24, с возможным выходом за пределы суток)
    для широты/долготы и даты.

    ``None`` — полярная ночь (солнце не встает); ``(0.0, 24.0)`` — полярный день.
    """
    day_of_year = on_date.timetuple().tm_yday
    decl = solar_declination_deg(day_of_year)

    cos_omega0 = -math.tan(math.radians(lat)) * math.tan(math.radians(decl))
    if cos_omega0 >= 1.0:
        return None  # полярная ночь
    if cos_omega0 <= -1.0:
        return (0.0, 24.0)  # полярный день

    omega0 = math.degrees(math.acos(cos_omega0))
    solar_noon_utc = 12.0 - lon / 15.0

    sunrise = solar_noon_utc - omega0 / 15.0
    sunset = solar_noon_utc + omega0 / 15.0
    return (sunrise, sunset)


def work_window_utc(
    lat: float,
    lon: float,
    day: date,
    window_start: time | None = None,
    window_end: time | None = None,
    tz: tzinfo | None = None,
) -> tuple[datetime, datetime] | None:
    """Действующее окно работ на местный календарный день ``day``: пересечение
    окна оператора со световым днем, в абсолютном времени (aware UTC).

    ``window_start``/``window_end`` — местное время в ``tz`` (``None`` — край
    окна не ограничен, работает только световой день); ``tz=None`` — UTC,
    как у задач, созданных до появления часового пояса.

    Раньше окно считалось в часах UTC-суток и обрезалось по ``[0, 24]``: на
    восточных долготах (Владивосток — восход около −4.4 ч UTC летом) из-за
    этого терялась вся утренняя часть светового дня, а окно оператора
    «09:00–18:00» понималось как UTC, хотя интерфейс показывает время в
    местном поясе. Здесь сутки светового дня выбираются так, чтобы истинный
    полдень пришелся на местный день ``day``, а часы восхода/заката
    откладываются от него через ``timedelta`` — отрицательные и больше 24 ч
    значения корректно переходят на соседние UTC-сутки.

    ``None`` — рабочих часов в этот день нет (полярная ночь или пустое
    пересечение).
    """
    tz = tz or timezone.utc
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    # Сдвиг опорных UTC-суток так, чтобы полдень был именно в местном дне day
    # (для поясов далеко от «солнечного» — например, UTC+13 на долготе −172).
    noon_local_date = (base + timedelta(hours=12.0 - lon / 15.0)).astimezone(tz).date()
    base += timedelta(days=(day - noon_local_date).days)

    hours = daylight_window_utc_hours(lat, lon, base.date())
    if hours is None:
        return None
    if hours == (0.0, 24.0):
        # Полярный день: солнце не садится — светлые все местные сутки.
        start = datetime.combine(day, time(0), tzinfo=tz)
        end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    else:
        start = base + timedelta(hours=hours[0])
        end = base + timedelta(hours=hours[1])

    if window_start is not None:
        start = max(start, datetime.combine(day, window_start, tzinfo=tz))
    if window_end is not None:
        end = min(end, datetime.combine(day, window_end, tzinfo=tz))
    if start >= end:
        return None
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
