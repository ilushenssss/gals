"""Логика модуля «Парк БВС»: ПБС.ФТ.2-4 (проверки при загрузке), ПБС.ФТ.11
(загрузка, заменяющая текущий парк). См. docs/trebovania/Парк_БВС.md.

Формат файла — JSON-массив объектов или CSV с колонками: ``inventory_number``,
``model`` (ключ из справочника ``FLEET_MODELS``, например ``geoscan-201``),
``base_launch_site`` (необязательно), ``status`` (``Готов`` по умолчанию).
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

        valid = not problems
        instances.append(FleetInstance(
            inventory_number=inv,
            model_key=model_key,
            model_name=model.name if model else model_key,
            base_launch_site=base_site,
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
