"""Логика модуля «Парк БВС»: ПБС.ФТ.2-4 (проверки при загрузке), ПБС.ФТ.11
(загрузка, заменяющая текущий парк). См. docs/trebovania/Парк_БВС.md.

Формат файла — JSON-массив объектов или CSV с колонками: ``inventory_number``,
``model`` (ключ из справочника ``FLEET_MODELS``, например ``geoscan-201``),
``base_launch_site`` (необязательно, название площадки как текст),
``location_lat``/``location_lon`` (необязательно, WGS-84 — фактические
координаты, где сейчас стоит экземпляр; разные БВС одного парка могут
базироваться на разных площадках, это не привязано к обстановке задачи),
``status`` (``Готов`` по умолчанию).
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from uav_planner.fleet import FLEET_MODELS, READINESS_STATUSES

from .fleet_models import FleetDetail, FleetInstance, FleetIssue, FleetSummary

_fleet: FleetDetail | None = None


def _parse_records(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("instances", [])
        if not isinstance(data, list):
            raise ValueError("ожидался список экземпляров БВС (JSON-массив)")
        return data
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _to_summary(detail: FleetDetail) -> FleetSummary:
    return FleetSummary(**detail.model_dump(exclude={"instances"}))


def _parse_location(rec: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    """Разбирает ``location_lat``/``location_lon`` — координаты не заданы
    (оба поля пусты) — легитимно, экземпляр просто без известной локации;
    заданы частично или не парсятся/вне диапазона — ошибка (не молчаливое
    отбрасывание, тот же принцип, что и у прочих полей записи)."""
    lat_raw, lon_raw = rec.get("location_lat"), rec.get("location_lon")
    if lat_raw in (None, "") and lon_raw in (None, ""):
        return None, None, None
    try:
        lat, lon = float(lat_raw), float(lon_raw)
    except (TypeError, ValueError):
        return None, None, "некорректная локация: широта/долгота должны быть числами"
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None, None, "некорректная локация: широта вне [-90, 90] или долгота вне [-180, 180]"
    return lat, lon, None


def validate_and_store(raw: bytes) -> FleetSummary:
    records = _parse_records(raw)

    seen_numbers: set[str] = set()
    instances: list[FleetInstance] = []
    errors: list[FleetIssue] = []

    for rec in records:
        inv = str(rec.get("inventory_number") or "").strip()
        model_key = str(rec.get("model") or "").strip()
        base_site = (rec.get("base_launch_site") or None) or None
        status = str(rec.get("status") or "Готов").strip()
        location_lat, location_lon, location_problem = _parse_location(rec)

        problems: list[str] = []
        if not inv:
            problems.append("не указан инвентарный номер")
        elif inv in seen_numbers:
            problems.append(f"дублирующийся инвентарный номер «{inv}»")
        else:
            seen_numbers.add(inv)

        model = FLEET_MODELS.get(model_key)
        if model is None:
            problems.append(f"неизвестная модель «{model_key}»; допустимо: {', '.join(FLEET_MODELS)}")

        if status not in READINESS_STATUSES:
            problems.append(f"недопустимый статус готовности «{status}»; допустимо: {', '.join(READINESS_STATUSES)}")

        if location_problem:
            problems.append(location_problem)

        valid = not problems
        instances.append(FleetInstance(
            inventory_number=inv,
            model_key=model_key,
            model_name=model.name if model else model_key,
            base_launch_site=base_site,
            location_lat=location_lat,
            location_lon=location_lon,
            status=status if status in READINESS_STATUSES else "Готов",
            valid=valid,
            error="; ".join(problems) if problems else None,
        ))
        for p in problems:
            errors.append(FleetIssue(inventory_number=inv or None, message=p))

    ready_count = sum(1 for i in instances if i.valid and i.status == "Готов")

    detail = FleetDetail(
        id=str(uuid.uuid4()),
        uploaded_at=datetime.now(timezone.utc),
        status="Содержит ошибки" if errors else "Корректна",
        total=len(instances),
        ready_count=ready_count,
        errors=errors,
        instances=instances,
    )

    global _fleet
    _fleet = detail
    return _to_summary(detail)


def get_fleet() -> FleetDetail:
    if _fleet is None:
        raise KeyError("парк БВС не загружен")
    return _fleet


def eligible_instances() -> list[FleetInstance]:
    """ПБС.ФТ.4: экземпляры со статусом «Готов», допустимые как кандидаты на задачу."""
    if _fleet is None:
        return []
    return [i for i in _fleet.instances if i.valid and i.status == "Готов"]
