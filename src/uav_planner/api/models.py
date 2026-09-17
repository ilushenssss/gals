"""Pydantic-схемы API модуля «Обстановка» — первая версия интерфейса.

Соответствует docs/trebovania/Обстановка.md (ОБС.ФТ.5-10). Формат входного
файла — GeoJSON FeatureCollection со свойством ``layer`` у каждого объекта:
``launch_site``, ``airspace``, ``no_fly``, ``obstacle``, ``reserve_site``.

Дополнительные свойства объектов:
  - airspace, obstacle: ``h_min``, ``h_max`` (числа, метры) — обязательны;
  - no_fly, obstacle: ``safety_buffer_m`` (число, метры) — необязательно, по умолчанию 0;
  - airspace, no_fly: ``active_windows`` — список {"start": ISO8601, "end": ISO8601},
    необязательно (без него зона активна всегда);
  - launch_site, reserve_site: ``name`` (строка) — необязательно.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

Layer = Literal["launch_site", "airspace", "no_fly", "obstacle", "reserve_site"]
Status = Literal["Корректна", "Содержит ошибки"]


class ValidationIssue(BaseModel):
    layer: Layer
    feature_index: int
    message: str
    name: str | None = None


class LayerCounts(BaseModel):
    launch_site: int = 0
    airspace: int = 0
    no_fly: int = 0
    obstacle: int = 0
    reserve_site: int = 0


class EnvironmentSummary(BaseModel):
    id: str
    name: str
    uploaded_at: datetime
    status: Status
    counts: LayerCounts
    errors: list[ValidationIssue] = []


class EnvironmentDetail(EnvironmentSummary):
    # GeoJSON Feature по каждому слою, WGS-84 — для отображения на карте.
    # У каждого Feature в properties добавлен признак "_valid": bool.
    layers: dict[Layer, list[dict[str, Any]]]
