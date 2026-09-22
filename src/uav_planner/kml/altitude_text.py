"""Разбор свободного текста диапазона высот из примерных KML-файлов кейса
(см. "Московская зона.kml") — в самом файле нет стандартного формата записи
диапазона высот (это прямо оговорено авторами примера в
docs/examples/Описание файлов.docx: "Предложите свой"), поэтому парсер
собран по образцам реальных строк из файла, а не по спецификации:

  - "От <низ> до <верх>" / "От земли до <верх>" / "На всех высотах";
  - высота — либо эшелон (``FL090`` = 9000 футов по стандартной атмосфере),
    либо метры с пояснением в футах в скобках (например,
    "800 м (2700 фут) AMSL") — берется число перед "м", а не футы: это
    авторское округление в метрах, а не наш перевод;
  - суффиксы ``AMSL``/``AGL``/"относительно уровня земли" не различаются —
    см. docstring ``kml.environment`` про то, почему v1 не строит модель
    рельефа и просто использует число как есть.

Если строку не удалось разобрать, функция возвращает ``None`` — вызывающая
сторона не проставляет часть, требующую этого текста, и это становится
обычной, видимой оператору проблемой (например, "неизвестна высота — зона
включена в обстановку без учета высотного диапазона"), а не тихой ошибкой.
"""

from __future__ import annotations

import re

FEET_TO_M = 0.3048
FEET_PER_FLIGHT_LEVEL = 100  # 1 эшелон (FL) = 100 футов истинной высоты

# FL999 — условное обозначение "практически неограниченная высота", принятое
# в аэронавигационных данных; используется, когда текст говорит "на всех
# высотах" без явного числового потолка.
UNBOUNDED_CEILING_M = 999 * FEET_PER_FLIGHT_LEVEL * FEET_TO_M

_FL_RE = re.compile(r"FL\s*0*(\d+)", re.IGNORECASE)
_METERS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*м\b", re.IGNORECASE)
_GROUND_RE = re.compile(r"земл[иье]", re.IGNORECASE)
_UNBOUNDED_RE = re.compile(r"на\s+всех\s+высотах", re.IGNORECASE)
_SPLIT_RE = re.compile(r"\s+до\s+", re.IGNORECASE)


def _parse_single(token: str) -> float | None:
    if _GROUND_RE.search(token):
        return 0.0
    m = _FL_RE.search(token)
    if m:
        return int(m.group(1)) * FEET_PER_FLIGHT_LEVEL * FEET_TO_M
    m = _METERS_RE.search(token)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def parse_altitude_range(text: str | None) -> tuple[float, float] | None:
    """Возвращает ``(h_min, h_max)`` в метрах или ``None``, если первая
    строка текста не разобрана. Только первая строка: в файле после диапазона
    высот через пустую строку часто идут дополнительные условия/исключения
    свободным текстом (например, "...до FL260\\n\\nИсключая границы района
    аэродрома Липецк..."), которые этот парсер не разбирает."""
    if not text:
        return None
    first_line = text.strip().splitlines()[0].strip()
    if _UNBOUNDED_RE.search(first_line):
        return 0.0, UNBOUNDED_CEILING_M
    parts = _SPLIT_RE.split(first_line, maxsplit=1)
    if len(parts) != 2:
        return None
    bottom = _parse_single(parts[0])
    top = _parse_single(parts[1])
    if bottom is None or top is None or bottom > top:
        return None
    return bottom, top
