"""Построение KML/GeoJSON для модуля «Подтверждение и экспорт» — ЭКС.ФТ.4,
ЭКС.ФТ.7-8. Работает только с уже посчитанным ``PlanDetail`` (координаты в
WGS-84, как их хранит ``plan_service``) — доступность экспорта (план должен
быть в статусе «Подтвержден»/«Выгружен») проверяет ``export_routes``, эти
функции сами статус не проверяют и годятся для любого плана.

Известная несостыковка в требованиях (не ошибка реализации): ЭКС.ФТ.8 ссылается
на таблицу свойств GeoJSON-объектов «см. «Методы», раздел 7» — в
``docs/realization/uav_planner_methods.html`` раздел 7 называется «Перелеты и
достижимость площадок» и такой таблицы не содержит. Настоящая таблица (и
структура KML) — раздел 15 того же документа («Состав экспортируемых
файлов»), спроектирована по уже имеющимся полям ``PlanSortie``/
``PlanSortiePhase``, а не наугад; текст самого требования не правился задним
числом.

Другое v1-ограничение: ни маршрут вылета, ни этапы не хранят высотный
профиль (взлет/набор/снижение) — вся расчетная модель ведет высоту съемки
как одну константу на вылет (``PlanDetail.height_m``, см. также
``uav_planner.geometry.Obstacle.is_hole_at``). Поэтому во всех экспортных
геометриях (кроме «Зоны покрытия» — она проекция на землю) третья координата
— это ``plan.height_m``, а не honest altitude profile.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from uav_planner.geometry import Projector

from .plan_models import PlanDetail, PlanSortie


def _sorties_by_uav(plan: PlanDetail) -> dict[str, list[PlanSortie]]:
    by_uav: dict[str, list[PlanSortie]] = {}
    for s in plan.sorties:
        by_uav.setdefault(s.uav_id, []).append(s)
    return by_uav


def _sorties_for_uav(plan: PlanDetail, uav_id: str) -> list[PlanSortie]:
    sorties = _sorties_by_uav(plan).get(uav_id)
    if not sorties:
        raise KeyError(f"БВС {uav_id!r} не найден в плане {plan.id}")
    return sorties


def _add_altitude(coords: Any, alt: float) -> Any:
    """Достраивает третью координату (высоту) ко всем точкам GeoJSON-геометрии
    любой вложенности — Point/LineString/MultiLineString/Polygon (ЭКС.ФТ.4:
    экспорт обязан отдавать `[lon, lat, alt]`, а внутренние геометрии плана
    хранятся 2D)."""
    if coords and isinstance(coords[0], (int, float)):
        return [coords[0], coords[1], alt]
    return [_add_altitude(c, alt) for c in coords]


def _key_points(sortie: PlanSortie, projector: Projector) -> list[dict]:
    """Ключевые точки вылета (взлет, границы каждого этапа, посадка).

    ``PlanSortiePhase`` не хранит собственные координаты (модуль
    «Планирование» хранит только суммарный маршрут вылета целиком), поэтому
    точки честно восстанавливаются интерполяцией вдоль фактического
    маршрута (``track_geojson``) пропорционально накопленному пройденному
    расстоянию — весь вылет летится на одной крейсерской скорости (см.
    ``plan_service._build_sortie_legs``), поэтому расстояние вдоль маршрута и
    время линейно связаны, и интерполяция по длине физически точна, а не
    приближение."""
    if not sortie.phases:
        return []
    route_utm = projector.to_utm(shape(sortie.track_geojson))
    total_len = route_utm.length
    total_dist = sum(p.distance_m for p in sortie.phases) or 1.0
    scale = total_len / total_dist

    points: list[dict] = [{
        "label": sortie.phases[0].label, "kind": sortie.phases[0].kind,
        "utc": sortie.phases[0].start_utc, "point": projector.to_wgs84(route_utm.interpolate(0.0)),
    }]
    cumulative = 0.0
    for phase in sortie.phases:
        cumulative += phase.distance_m
        pt_utm = route_utm.interpolate(min(cumulative * scale, total_len))
        points.append({
            "label": phase.label, "kind": phase.kind,
            "utc": phase.end_utc, "point": projector.to_wgs84(pt_utm),
        })
    return points


def _coverage_polygon(sortie: PlanSortie, swath_m: float, projector: Projector):
    """«Зона покрытия» вылета — полоса шириной ``swath_m`` вдоль галсов
    (без переходов), объединенная в один полигон. Приближение: реальная
    зона покрытия зависит от ориентации кадра камеры, здесь взята
    симметричная полоса вокруг линии галса (та же модель, что использует
    ``safety.check_coverage`` для рабочей области)."""
    survey_utm = projector.to_utm(shape(sortie.survey_tracks_geojson))
    lines = list(survey_utm.geoms) if survey_utm.geom_type == "MultiLineString" else [survey_utm]
    lines = [line for line in lines if not line.is_empty]
    if not lines:
        return None
    return unary_union([line.buffer(swath_m / 2.0, cap_style=2) for line in lines])


def to_geojson(plan: PlanDetail, uav_id: str) -> dict:
    """ЭКС.ФТ.7-8: GeoJSON ``FeatureCollection`` для одного БВС — «Вылет»
    (LineString), «Ключевая точка» (Point), «Галсы вылета» (MultiLineString),
    «Зона покрытия» (Polygon), по одному набору на каждый вылет этого БВС."""
    sorties = _sorties_for_uav(plan, uav_id)
    projector = Projector.for_geometry(shape(sorties[0].track_geojson))

    features: list[dict] = []
    for sortie in sorties:
        features.append({
            "type": "Feature",
            "properties": {
                "type": "Вылет", "uav_id": sortie.uav_id, "sortie_index": sortie.sortie_index,
                "takeoff_site": sortie.takeoff_site, "landing_site": sortie.landing_site,
                "start_utc": sortie.start_utc.isoformat(), "end_utc": sortie.end_utc.isoformat(),
                "flight_time_s": sortie.flight_time_s, "distance_m": sortie.distance_m,
                "speed_mps": plan.cruise_speed_mps, "altitude_m": plan.height_m,
            },
            "geometry": {
                "type": sortie.track_geojson["type"],
                "coordinates": _add_altitude(sortie.track_geojson["coordinates"], plan.height_m),
            },
        })
        features.append({
            "type": "Feature",
            "properties": {
                "type": "Галсы вылета", "uav_id": sortie.uav_id, "sortie_index": sortie.sortie_index,
                "swath_m": plan.swath_m, "altitude_m": plan.height_m,
            },
            "geometry": {
                "type": sortie.survey_tracks_geojson["type"],
                "coordinates": _add_altitude(sortie.survey_tracks_geojson["coordinates"], plan.height_m),
            },
        })
        for i, kp in enumerate(_key_points(sortie, projector)):
            features.append({
                "type": "Feature",
                "properties": {
                    "type": "Ключевая точка", "uav_id": sortie.uav_id, "sortie_index": sortie.sortie_index,
                    "sequence": i, "label": kp["label"], "kind": kp["kind"], "utc": kp["utc"].isoformat(),
                },
                "geometry": {"type": "Point", "coordinates": [kp["point"].x, kp["point"].y, plan.height_m]},
            })
        coverage = _coverage_polygon(sortie, plan.swath_m, projector)
        if coverage is not None and not coverage.is_empty:
            coverage_wgs84 = projector.to_wgs84(coverage)
            coverage_mapping = mapping(coverage_wgs84)
            features.append({
                "type": "Feature",
                "properties": {
                    "type": "Зона покрытия", "uav_id": sortie.uav_id, "sortie_index": sortie.sortie_index,
                    "swath_m": plan.swath_m,
                },
                "geometry": {
                    "type": coverage_mapping["type"],
                    "coordinates": _add_altitude(coverage_mapping["coordinates"], 0.0),
                },
            })
    return {"type": "FeatureCollection", "features": features}


def _kml_coords(coords: list[tuple[float, float]], alt: float) -> str:
    return " ".join(f"{lon:.7f},{lat:.7f},{alt:.1f}" for lon, lat in coords)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kml_extended_data(fields: dict[str, Any]) -> str:
    items = "".join(
        f'<Data name="{xml_escape(str(k))}"><value>{xml_escape(str(v))}</value></Data>'
        for k, v in fields.items()
    )
    return f"<ExtendedData>{items}</ExtendedData>"


def to_kml(plan: PlanDetail, uav_id: str) -> str:
    """ЭКС.ФТ.7-8: KML ``Document -> Folder(БВС) -> Folder(вылет)`` с
    Placemark «Маршрут» (LineString, ``altitudeMode=relativeToGround``,
    ``TimeSpan``), Placemark «Галсы» (``MultiGeometry`` линий) и по одному
    Placemark на каждую ключевую точку (``Point``, ``TimeStamp``), этап/
    скорость/высота — в ``ExtendedData``."""
    sorties = _sorties_for_uav(plan, uav_id)
    projector = Projector.for_geometry(shape(sorties[0].track_geojson))

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        f"<name>{xml_escape(uav_id)} — план {xml_escape(plan.task_id)} версии {plan.version}</name>",
        f"<Folder><name>{xml_escape(uav_id)}</name>",
    ]
    for sortie in sorties:
        parts.append(f"<Folder><name>Вылет {sortie.sortie_index + 1}</name>")

        route_coords = sortie.track_geojson["coordinates"]
        parts.append(
            "<Placemark><name>Маршрут</name>"
            f"<TimeSpan><begin>{_iso(sortie.start_utc)}</begin><end>{_iso(sortie.end_utc)}</end></TimeSpan>"
            + _kml_extended_data({
                "uav_id": uav_id, "speed_mps": round(plan.cruise_speed_mps, 2),
                "altitude_m": round(plan.height_m, 1),
            })
            + "<LineString><altitudeMode>relativeToGround</altitudeMode>"
            f"<coordinates>{_kml_coords(route_coords, plan.height_m)}</coordinates></LineString>"
            "</Placemark>"
        )

        survey_lines = sortie.survey_tracks_geojson["coordinates"]
        multi = "".join(
            "<LineString><altitudeMode>relativeToGround</altitudeMode>"
            f"<coordinates>{_kml_coords(line, plan.height_m)}</coordinates></LineString>"
            for line in survey_lines
        )
        parts.append(f"<Placemark><name>Галсы</name><MultiGeometry>{multi}</MultiGeometry></Placemark>")

        for kp in _key_points(sortie, projector):
            lon, lat = kp["point"].x, kp["point"].y
            parts.append(
                f"<Placemark><name>{xml_escape(kp['label'])}</name>"
                f"<TimeStamp><when>{_iso(kp['utc'])}</when></TimeStamp>"
                + _kml_extended_data({
                    "этап": kp["kind"], "speed_mps": round(plan.cruise_speed_mps, 2),
                    "altitude_m": round(plan.height_m, 1),
                })
                + "<Point><altitudeMode>relativeToGround</altitudeMode>"
                f"<coordinates>{lon:.7f},{lat:.7f},{plan.height_m:.1f}</coordinates></Point>"
                "</Placemark>"
            )
        parts.append("</Folder>")
    parts.append("</Folder></Document></kml>")
    return "".join(parts)


def to_zip(plan: PlanDetail) -> bytes:
    """ЭКС.ФТ.7 «Скачать все» — архив с KML и GeoJSON по каждому БВС группы."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for uav_id in _sorties_by_uav(plan):
            zf.writestr(f"{uav_id}.kml", to_kml(plan, uav_id))
            zf.writestr(f"{uav_id}.geojson", json.dumps(to_geojson(plan, uav_id), ensure_ascii=False, indent=2))
    return buf.getvalue()
