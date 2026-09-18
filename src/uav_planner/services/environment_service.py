"""Импорт и проверка обстановки — реализация ОБС.ФТ.2, ОБС.ФТ.3, ОБС.ФТ.4, ОБС.ФТ.8, ОБС.ФТ.9.

См. docs/trebovania/Обстановка.md. Доступ к хранилищу — через
``uav_planner.repositories``; его текущая реализация держит данные в памяти
процесса (после перезапуска сервера обстановки нужно загрузить заново) и
заменяется на PostGIS без изменений в этом модуле.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.geometry import (
    GeometryError,
    HeightRange,
    NoFlyZone,
    Projector,
    TimeWindow,
    validate_polygon,
)

from uav_planner.api.schemas.environment import EnvironmentDetail, EnvironmentSummary, LayerCounts, ValidationIssue

LAYER_TYPES = ("launch_site", "airspace", "no_fly", "obstacle", "reserve_site")
POLYGON_LAYERS = ("airspace", "no_fly", "obstacle")
POINT_LAYERS = ("launch_site", "reserve_site")



def _parse_time_windows(raw: Any) -> list[TimeWindow] | None:
    if not raw:
        return None
    return [
        TimeWindow(start=datetime.fromisoformat(w["start"]), end=datetime.fromisoformat(w["end"]))
        for w in raw
    ]


def _group_features(geojson: dict) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {layer: [] for layer in LAYER_TYPES}
    features = geojson.get("features")
    if not isinstance(features, list):
        raise ValueError("файл должен быть GeoJSON FeatureCollection со списком 'features'")
    for feature in features:
        layer = (feature.get("properties") or {}).get("layer")
        if layer not in LAYER_TYPES:
            raise ValueError(
                f"у объекта отсутствует или указан неизвестный layer: {layer!r} "
                f"(допустимо: {', '.join(LAYER_TYPES)})"
            )
        groups[layer].append(feature)
    return groups


def _validate_polygon_feature(layer: str, feature: dict) -> BaseGeometry:
    """ОБС.ФТ.2 (валидность) + ОБС.ФТ.4 (диапазоны). Бросает исключение при нарушении."""
    props = feature.get("properties") or {}
    geom = shape(feature["geometry"])
    validate_polygon(geom)
    if layer in ("airspace", "obstacle"):
        for field in ("h_min", "h_max"):
            if field not in props:
                raise ValueError(f"отсутствует обязательное поле '{field}' (диапазон высот)")
        HeightRange(float(props["h_min"]), float(props["h_max"]))
    _parse_time_windows(props.get("active_windows"))
    return geom


def _validate_point_feature(feature: dict) -> BaseGeometry:
    geom = shape(feature["geometry"])
    if geom.geom_type != "Point":
        raise ValueError(f"ожидалась точка (Point), получено {geom.geom_type}")
    return geom


def validate_and_store(geojson: dict, name: str) -> EnvironmentSummary:
    """Парсит GeoJSON, выполняет все проверки (ОБС.ФТ.2-4) и сохраняет результат.

    Невалидные объекты не отбрасываются: они помечаются
    ``properties["_valid"] = False`` и остаются в ``layers`` — так фронтенд может
    показать их на карте выделенными как ошибочные (см. ИНТ.ФТ.7).
    """
    groups = _group_features(geojson)
    errors: list[ValidationIssue] = []
    geoms: dict[tuple[str, int], BaseGeometry] = {}

    for layer in POLYGON_LAYERS:
        for idx, feature in enumerate(groups[layer]):
            props = feature.setdefault("properties", {})
            try:
                geoms[(layer, idx)] = _validate_polygon_feature(layer, feature)
                props["_valid"] = True
            except (GeometryError, KeyError, ValueError, TypeError) as exc:
                props["_valid"] = False
                errors.append(
                    ValidationIssue(layer=layer, feature_index=idx, message=str(exc), name=props.get("name"))
                )

    for layer in POINT_LAYERS:
        for idx, feature in enumerate(groups[layer]):
            props = feature.setdefault("properties", {})
            try:
                geoms[(layer, idx)] = _validate_point_feature(feature)
                props["_valid"] = True
            except (ValueError, TypeError) as exc:
                props["_valid"] = False
                errors.append(
                    ValidationIssue(layer=layer, feature_index=idx, message=str(exc), name=props.get("name"))
                )

    # ОБС.ФТ.3: согласованность ВПП/резервных площадок с разрешенным пространством и БПЗ.
    if geoms:
        projector = Projector.for_geometry(unary_union(list(geoms.values())))

        allowed_geoms = [projector.to_utm(g) for (l, i), g in geoms.items() if l == "airspace"]
        allowed_union = unary_union(allowed_geoms) if allowed_geoms else None

        no_fly_zones: list[tuple[int, NoFlyZone]] = []
        for idx, feature in enumerate(groups["no_fly"]):
            key = ("no_fly", idx)
            if key not in geoms:
                continue
            props = feature["properties"]
            buffer_m = float(props.get("safety_buffer_m", 0.0))
            nfz = NoFlyZone(id=str(idx), polygon=projector.to_utm(geoms[key]), safety_buffer_m=buffer_m)
            no_fly_zones.append((idx, nfz))
            if buffer_m > 0:
                # Даем фронтенду точный контур буфера, а не только исходный полигон.
                props["_buffer_geojson"] = mapping(projector.to_wgs84(nfz.footprint()))

        for layer in POINT_LAYERS:
            for idx, feature in enumerate(groups[layer]):
                key = (layer, idx)
                if key not in geoms or not feature["properties"]["_valid"]:
                    continue
                props = feature["properties"]
                point_utm = projector.to_utm(geoms[key])

                if allowed_union is not None and not point_utm.within(allowed_union):
                    props["_valid"] = False
                    errors.append(
                        ValidationIssue(
                            layer=layer, feature_index=idx,
                            message="точка вне разрешенного воздушного пространства",
                            name=props.get("name"),
                        )
                    )
                    continue

                for nfz_idx, nfz in no_fly_zones:
                    if point_utm.intersects(nfz.footprint()):
                        props["_valid"] = False
                        errors.append(
                            ValidationIssue(
                                layer=layer, feature_index=idx,
                                message=f"точка внутри бесполетной зоны #{nfz_idx} (с буфером {nfz.safety_buffer_m} м)",
                                name=props.get("name"),
                            )
                        )
                        break

    counts = LayerCounts(**{layer: len(groups[layer]) for layer in LAYER_TYPES})
    env_id = str(uuid.uuid4())
    detail = EnvironmentDetail(
        id=env_id,
        name=name,
        uploaded_at=datetime.now(timezone.utc),
        status="Содержит ошибки" if errors else "Корректна",
        counts=counts,
        errors=errors,
        layers=groups,
    )
    repositories.environments.put(env_id, detail)
    return EnvironmentSummary(**detail.model_dump(exclude={"layers"}))


def list_environments() -> list[EnvironmentSummary]:
    return [
        EnvironmentSummary(**env.model_dump(exclude={"layers"}))
        for env in repositories.environments.list_newest_first()
    ]


def get_environment(environment_id: str) -> EnvironmentDetail:
    return repositories.environments.get(environment_id)
