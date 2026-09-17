"""Восход/закат по приближенным астрономическим формулам.

См. docs/trebovania/Математическая_модель.md, раздел 14. Модель не учитывает
уравнение времени (±16 мин) и относится к местному солнечному времени,
скорректированному только по долготе (без часовых зон) — этого достаточно,
чтобы проверить, пересекается ли окно работ со световым днем (ЗАД.ФТ.3).
"""

from __future__ import annotations

import math
from datetime import date


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
