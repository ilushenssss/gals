"""Логика модуля «Парк БВС»: ПБС.ФТ.2-4 (проверки при загрузке экземпляров).
См. docs/trebovania/Парк_БВС.md.

v1-расширение по запросу пользователя: парков теперь несколько, каждый —
отдельная именованная сущность со своей локацией (не единственный глобальный
парк, который загрузка заменяла целиком, как было в ПБС.ФТ.11 изначально) —
``_fleets`` хранит их все, ключ — id парка. Локация парка не вводится вручную
при создании, а определяется из самого файла (см. ``_derive_fleet_location``)
— центроид координат экземпляров, у которых они указаны.

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

_fleets: dict[str, FleetDetail] = {}


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


class FleetLocationError(ValueError):
    """Не удалось определить локацию парка по файлу (ни у одного экземпляра
    не указаны координаты) — см. ``_derive_fleet_location``."""


def _derive_fleet_location(instances: list[FleetInstance]) -> tuple[float, float]:
    """Локация парка в целом — по запросу пользователя определяется из самого
    файла парка (координаты экземпляров), а не вводится вручную: центроид
    (среднее) локаций всех экземпляров, у которых она указана и корректна
    (``FleetInstance.location_lat/lon`` — уже провалидированы `_parse_location`
    выше на этапе разбора записей). Простое среднее, без географической
    поправки на сближение меридианов — честная v1-оценка: в реальных файлах
    экземпляры одного парка стоят близко друг к другу (одна или несколько
    площадок в одном районе), а не на разных концах света."""
    located = [(i.location_lat, i.location_lon) for i in instances if i.location_lat is not None and i.location_lon is not None]
    if not located:
        raise FleetLocationError(
            "не удалось определить локацию парка: ни у одного экземпляра в файле "
            "не указаны координаты (location_lat/location_lon)"
        )
    lat = sum(p[0] for p in located) / len(located)
    lon = sum(p[1] for p in located) / len(located)
    return lat, lon


def create_fleet(*, name: str, location_name: str | None, raw: bytes) -> FleetSummary:
    records = _parse_records(raw)

    seen_numbers: set[str] = set()
    instances: list[FleetInstance] = []
    errors: list[FleetIssue] = []

    for rec in records:
        inv = str(rec.get("inventory_number") or "").strip()
        model_key = str(rec.get("model") or "").strip()
        base_site = (rec.get("base_launch_site") or None) or None
        status = str(rec.get("status") or "Готов").strip()
        # Локация ЭКЗЕМПЛЯРА (может отличаться от локации парка в целом,
        # см. FleetInstance.location_lat/lon) — специально не переиспользует
        # имена location_lat/lon параметров функции, чтобы не затереть
        # локацию самого парка на следующих итерациях цикла.
        inst_lat, inst_lon, location_problem = _parse_location(rec)

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
            location_lat=inst_lat,
            location_lon=inst_lon,
            status=status if status in READINESS_STATUSES else "Готов",
            valid=valid,
            error="; ".join(problems) if problems else None,
        ))
        for p in problems:
            errors.append(FleetIssue(inventory_number=inv or None, message=p))

    ready_count = sum(1 for i in instances if i.valid and i.status == "Готов")
    location_lat, location_lon = _derive_fleet_location(instances)

    detail = FleetDetail(
        id=str(uuid.uuid4()),
        name=name,
        location_lat=location_lat,
        location_lon=location_lon,
        location_name=location_name,
        uploaded_at=datetime.now(timezone.utc),
        status="Содержит ошибки" if errors else "Корректна",
        total=len(instances),
        ready_count=ready_count,
        errors=errors,
        instances=instances,
    )

    _fleets[detail.id] = detail
    return _to_summary(detail)


def list_fleets() -> list[FleetSummary]:
    return [_to_summary(f) for f in sorted(_fleets.values(), key=lambda f: f.uploaded_at, reverse=True)]


def get_fleet(fleet_id: str) -> FleetDetail:
    return _fleets[fleet_id]  # KeyError -> 404 в routes


def eligible_instances(fleet_id: str) -> list[FleetInstance]:
    """ПБС.ФТ.4: экземпляры выбранного парка со статусом «Готов», допустимые
    как кандидаты на задачу (задача теперь ссылается на конкретный парк —
    ``TaskDetail.fleet_id`` — а не на единственный глобальный, см. модуль
    «Задача»)."""
    fleet = _fleets.get(fleet_id)
    if fleet is None:
        return []
    return [i for i in fleet.instances if i.valid and i.status == "Готов"]
