"""Формирование файлов экспорта — ЭКС.ФТ.4, ЭКС.ФТ.7-8.

Чистые функции над ``PlanDetail``: ни БД, ни HTTP. Это осознанно — файл
экспорта обязан быть воспроизводим байт в байт по неизменяемому плану
(ЭКС.ФТ.3), поэтому сами файлы нигде не хранятся, а таблица
``export_artifacts`` ведет только журнал выгрузок.

Координаты — WGS-84 (ЭКС.ФТ.4): план уже хранится в WGS-84, обратное
проецирование не требуется. UTM здесь нужен ровно для двух метрических
операций — длин вдоль маршрута (интерполяция времени прохода) и буфера полосы
захвата (зона покрытия); результат обеих возвращается в WGS-84.

**Чего в плане v1 нет и как это честно восполнено.** Расчетное ядро сохраняет
на вылет только линию маршрута, линии галсов и время старта/посадки —
отдельного списка ключевых точек с этапом и временем прохода (``waypoints``
из ЭКС.ФТ.8) в нем пока не существует. Поэтому ключевые точки выводятся из
самого маршрута:

* точка — вершина линии маршрута;
* этап: первая — «Взлет», последняя — «Посадка», совпадающая с вершиной
  галса — «Съемка», остальные — «Перелет»;
* время прохода — линейная интерполяция между ``start_utc`` и ``end_utc`` по
  накопленной метрической длине, что соответствует принятой в v1 постоянной
  крейсерской скорости (ускорения, развороты и ожидание на земле появятся
  вместе с модулем ``motion``).

Набор свойств GeoJSON ТЗ выносит в документ «Методы, раздел 7», которого у нас
нет; зафиксированный здесь набор описан в README (ответы экспертов, п. 22).
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree as ET

from shapely.geometry import LineString, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from uav_planner import repositories
from uav_planner.api.schemas.plan import PlanDetail, PlanSortie
from uav_planner.domain.errors import ValidationError
from uav_planner.geometry import Projector

KML_NS = "http://www.opengis.net/kml/2.2"
KML_MEDIA_TYPE = "application/vnd.google-earth.kml+xml"
GEOJSON_MEDIA_TYPE = "application/geo+json"
ZIP_MEDIA_TYPE = "application/zip"

STAGE_TAKEOFF = "Взлет"
STAGE_TRANSIT = "Перелет"
STAGE_SURVEY = "Съемка"
STAGE_LANDING = "Посадка"

# Сравнение вершин маршрута с вершинами галсов — по округленным координатам:
# и те, и другие получены одним обратным проецированием из одной точки UTM,
# но проходят через разные геометрии, и точного равенства float ждать нельзя.
# 1e-9 градуса — доли миллиметра, заведомо меньше любого реального различия.
_COORD_PRECISION = 9


class ExportError(ValueError):
    """План нельзя выгрузить в запрошенном виде."""


def _round(coord: tuple[float, float]) -> tuple[float, float]:
    return (round(coord[0], _COORD_PRECISION), round(coord[1], _COORD_PRECISION))


def _iso(moment: datetime) -> str:
    """ISO 8601 в UTC с суффиксом Z — формат, который требует ЭКС.ФТ.8."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def uav_ids(plan: PlanDetail) -> list[str]:
    """Состав группы в порядке появления — список для кнопок ЭКС.ФТ.7."""
    return list(dict.fromkeys(s.uav_id for s in plan.sorties))


def _sorties_of(plan: PlanDetail, uav_id: str | None) -> list[PlanSortie]:
    if uav_id is None:
        return list(plan.sorties)
    sorties = [s for s in plan.sorties if s.uav_id == uav_id]
    if not sorties:
        raise ExportError(f"в плане нет вылетов БВС «{uav_id}»")
    return sorties


def _projector_for(plan: PlanDetail) -> Projector:
    geoms = [shape(s.track_geojson) for s in plan.sorties]
    return Projector.for_geometry(unary_union(geoms))


def waypoints_of(sortie: PlanSortie, plan: PlanDetail, projector: Projector) -> list[dict[str, Any]]:
    """Ключевые точки вылета: этап, время прохода, высота, скорость."""
    route = shape(sortie.track_geojson)
    coords = list(route.coords)
    survey = shape(sortie.survey_tracks_geojson)
    survey_lines = list(survey.geoms) if survey.geom_type == "MultiLineString" else [survey]
    survey_vertices = {_round(c) for line in survey_lines for c in line.coords}

    route_utm = projector.to_utm(LineString(coords))
    utm_coords = list(route_utm.coords)
    cumulative = [0.0]
    for previous, point in zip(utm_coords, utm_coords[1:]):
        step = ((point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2) ** 0.5
        cumulative.append(cumulative[-1] + step)
    total = cumulative[-1] or 1.0
    duration = (sortie.end_utc - sortie.start_utc).total_seconds()

    last = len(coords) - 1
    points: list[dict[str, Any]] = []
    for index, coord in enumerate(coords):
        if index == 0:
            stage = STAGE_TAKEOFF
        elif index == last:
            stage = STAGE_LANDING
        elif _round(coord) in survey_vertices:
            stage = STAGE_SURVEY
        else:
            stage = STAGE_TRANSIT
        on_ground = stage in (STAGE_TAKEOFF, STAGE_LANDING)
        points.append({
            "index": index,
            "stage": stage,
            "lon": coord[0],
            "lat": coord[1],
            "altitude_m": 0.0 if on_ground else plan.height_m,
            "speed_mps": 0.0 if on_ground else plan.cruise_speed_mps,
            "time_utc": sortie.start_utc + timedelta(seconds=duration * cumulative[index] / total),
        })
    return points


def coverage_of(sortie: PlanSortie, plan: PlanDetail, projector: Projector) -> BaseGeometry | None:
    """Зона покрытия вылета — полоса захвата вдоль галсов (ЭКС.ФТ.8)."""
    survey = shape(sortie.survey_tracks_geojson)
    if survey.is_empty or plan.swath_m <= 0:
        return None
    buffered = projector.to_utm(survey).buffer(plan.swath_m / 2)
    return None if buffered.is_empty else projector.to_wgs84(buffered)


# --- KML --------------------------------------------------------------------


def _kml_coords(coords, altitude: float) -> str:
    return " ".join(f"{lon:.9f},{lat:.9f},{altitude:.1f}" for lon, lat in coords)


def _extended_data(parent: ET.Element, values: dict[str, Any]) -> None:
    container = ET.SubElement(parent, "ExtendedData")
    for name, value in values.items():
        node = ET.SubElement(container, "Data", {"name": name})
        ET.SubElement(node, "value").text = str(value)


def to_kml(plan: PlanDetail, uav_id: str | None = None) -> bytes:
    """KML по одному БВС (или по всей группе, если ``uav_id`` не задан).

    Структура ЭКС.ФТ.8: ``Document → Folder(БВС) → Folder(вылет)``, маршрут
    линией с ``altitudeMode=relativeToGround``, галсы — ``MultiGeometry``,
    ``TimeSpan`` на вылет и ``TimeStamp`` на ключевую точку.
    """
    sorties = _sorties_of(plan, uav_id)
    projector = _projector_for(plan)

    kml = ET.Element("kml", {"xmlns": KML_NS})
    document = ET.SubElement(kml, "Document")
    ET.SubElement(document, "name").text = f"План версии {plan.version}"
    _extended_data(document, {
        "Идентификатор плана": plan.id,
        "Задача": plan.task_id,
        "Версия": plan.version,
        "Модель БВС": plan.uav_model,
        "Высота съемки, м": round(plan.height_m, 1),
        "Полоса захвата, м": round(plan.swath_m, 1),
        "Крейсерская скорость, м/с": round(plan.cruise_speed_mps, 1),
        "Критерий": plan.criterion_mode,
    })

    for current_uav in dict.fromkeys(s.uav_id for s in sorties):
        uav_folder = ET.SubElement(document, "Folder")
        ET.SubElement(uav_folder, "name").text = f"БВС {current_uav}"

        for sortie in (s for s in sorties if s.uav_id == current_uav):
            sortie_folder = ET.SubElement(uav_folder, "Folder")
            ET.SubElement(sortie_folder, "name").text = f"Вылет {sortie.sortie_index + 1}"
            timespan = ET.SubElement(sortie_folder, "TimeSpan")
            ET.SubElement(timespan, "begin").text = _iso(sortie.start_utc)
            ET.SubElement(timespan, "end").text = _iso(sortie.end_utc)

            route = ET.SubElement(sortie_folder, "Placemark")
            ET.SubElement(route, "name").text = "Маршрут"
            _extended_data(route, {
                "БВС": sortie.uav_id,
                "Вылет": sortie.sortie_index + 1,
                "Площадка старта": sortie.takeoff_site or "",
                "Площадка посадки": sortie.landing_site or "",
                "Налет, с": round(sortie.flight_time_s, 1),
                "Длина галсов, м": round(sortie.distance_m, 1),
            })
            line = ET.SubElement(route, "LineString")
            ET.SubElement(line, "altitudeMode").text = "relativeToGround"
            ET.SubElement(line, "tessellate").text = "1"
            ET.SubElement(line, "coordinates").text = _kml_coords(
                shape(sortie.track_geojson).coords, plan.height_m
            )

            survey = shape(sortie.survey_tracks_geojson)
            survey_lines = list(survey.geoms) if survey.geom_type == "MultiLineString" else [survey]
            tracks = ET.SubElement(sortie_folder, "Placemark")
            ET.SubElement(tracks, "name").text = "Галсы"
            multi = ET.SubElement(tracks, "MultiGeometry")
            for track in survey_lines:
                track_line = ET.SubElement(multi, "LineString")
                ET.SubElement(track_line, "altitudeMode").text = "relativeToGround"
                ET.SubElement(track_line, "coordinates").text = _kml_coords(
                    track.coords, plan.height_m
                )

            for point in waypoints_of(sortie, plan, projector):
                placemark = ET.SubElement(sortie_folder, "Placemark")
                ET.SubElement(placemark, "name").text = (
                    f"Точка {point['index'] + 1} — {point['stage']}"
                )
                stamp = ET.SubElement(placemark, "TimeStamp")
                ET.SubElement(stamp, "when").text = _iso(point["time_utc"])
                _extended_data(placemark, {
                    "Этап": point["stage"],
                    "Высота, м": round(point["altitude_m"], 1),
                    "Скорость, м/с": round(point["speed_mps"], 1),
                })
                geometry = ET.SubElement(placemark, "Point")
                ET.SubElement(geometry, "altitudeMode").text = "relativeToGround"
                ET.SubElement(geometry, "coordinates").text = _kml_coords(
                    [(point["lon"], point["lat"])], point["altitude_m"]
                )

    ET.indent(kml, space="  ")
    return ET.tostring(kml, encoding="utf-8", xml_declaration=True)


# --- GeoJSON ----------------------------------------------------------------


def to_geojson(plan: PlanDetail, uav_id: str | None = None) -> dict[str, Any]:
    """FeatureCollection с объектами «Вылет», «Ключевая точка», «Галсы вылета»
    и «Зона покрытия» (ЭКС.ФТ.8). Координаты — WGS-84 (ЭКС.ФТ.4)."""
    sorties = _sorties_of(plan, uav_id)
    projector = _projector_for(plan)
    features: list[dict[str, Any]] = []

    for sortie in sorties:
        common = {
            "plan_id": plan.id,
            "plan_version": plan.version,
            "uav_id": sortie.uav_id,
            "sortie_index": sortie.sortie_index + 1,
        }
        features.append({
            "type": "Feature",
            "properties": {
                **common,
                "object": "Вылет",
                "takeoff_site": sortie.takeoff_site,
                "landing_site": sortie.landing_site,
                "start_utc": _iso(sortie.start_utc),
                "end_utc": _iso(sortie.end_utc),
                "flight_time_s": round(sortie.flight_time_s, 1),
                "survey_distance_m": round(sortie.distance_m, 1),
                "altitude_m": round(plan.height_m, 1),
                "speed_mps": round(plan.cruise_speed_mps, 1),
            },
            "geometry": sortie.track_geojson,
        })

        for point in waypoints_of(sortie, plan, projector):
            features.append({
                "type": "Feature",
                "properties": {
                    **common,
                    "object": "Ключевая точка",
                    "waypoint_index": point["index"] + 1,
                    "stage": point["stage"],
                    "time_utc": _iso(point["time_utc"]),
                    "altitude_m": round(point["altitude_m"], 1),
                    "speed_mps": round(point["speed_mps"], 1),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [point["lon"], point["lat"], point["altitude_m"]],
                },
            })

        features.append({
            "type": "Feature",
            "properties": {
                **common,
                "object": "Галсы вылета",
                "altitude_m": round(plan.height_m, 1),
                "swath_m": round(plan.swath_m, 1),
            },
            "geometry": sortie.survey_tracks_geojson,
        })

        coverage = coverage_of(sortie, plan, projector)
        if coverage is not None:
            features.append({
                "type": "Feature",
                "properties": {
                    **common,
                    "object": "Зона покрытия",
                    "swath_m": round(plan.swath_m, 1),
                },
                "geometry": mapping(coverage),
            })

    return {
        "type": "FeatureCollection",
        "properties": {
            "plan_id": plan.id,
            "task_id": plan.task_id,
            "plan_version": plan.version,
            "uav_model": plan.uav_model,
            "criterion_mode": plan.criterion_mode,
            "j1_s": round(plan.j1_s, 1),
            "j2_s": round(plan.j2_s, 1),
            "crs": "WGS 84 (EPSG:4326)",
        },
        "features": features,
    }


# --- имена файлов и архив ----------------------------------------------------


def _safe(fragment: str) -> str:
    """Инвентарный номер попадает в имя файла как есть, кроме разделителей.

    Кириллицу не транслитерируем: заголовок ответа отдается в RFC 5987, и
    браузер получает имя в UTF-8 без искажения.
    """
    for bad in '/\\:*?"<>|\n\r\t':
        fragment = fragment.replace(bad, "-")
    return fragment.strip() or "БВС"


def filename_for(plan: PlanDetail, uav_id: str | None, fmt: str) -> str:
    suffix = _safe(uav_id) if uav_id else "все-БВС"
    return f"план-в{plan.version}-{suffix}.{fmt}"


def to_zip(plan: PlanDetail) -> bytes:
    """«Скачать все» (ЭКС.ФТ.7): KML и GeoJSON по каждому БВС группы."""
    import json

    ids = uav_ids(plan)
    if not ids:
        raise ExportError("в плане нет ни одного вылета — выгружать нечего")

    buffer = io.BytesIO()
    # Детерминированный архив: без штампов времени файл, собранный дважды по
    # одному плану, совпадает байт в байт — это то же свойство, на котором
    # держится отказ хранить артефакты экспорта.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for current in ids:
            info = zipfile.ZipInfo(filename_for(plan, current, "kml"), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, to_kml(plan, current))

            info = zipfile.ZipInfo(filename_for(plan, current, "geojson"), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(
                info,
                json.dumps(to_geojson(plan, current), ensure_ascii=False, indent=2).encode("utf-8"),
            )
    return buffer.getvalue()


# --- операция экспорта (единственная часть модуля, знающая о хранилище) -----


class ExportNotAllowedError(ValidationError):
    """ЭКС.ФТ.7: выгружать можно только подтвержденный план."""


EXPORTABLE_STATUSES = ("Подтвержден", "Выгружен")


def export_plan(
    plan_id: str, fmt: str, uav_id: str | None, user: str
) -> tuple[bytes, str, str]:
    """Сформировать файл выгрузки и записать факт в журнал.

    Возвращает содержимое, имя файла и media type. Первая выгрузка переводит
    план в «Выгружен» (ЭКС.ФТ.5) — это делает репозиторий условным UPDATE.
    """
    import json

    plan = repositories.plans.get(plan_id)
    if plan.status not in EXPORTABLE_STATUSES:
        raise ExportNotAllowedError(
            f"план в статусе «{plan.status}» не выгружается — сначала подтвердите его"
        )

    if fmt == "kml":
        content, media_type = to_kml(plan, uav_id), KML_MEDIA_TYPE
    elif fmt == "geojson":
        content = json.dumps(
            to_geojson(plan, uav_id), ensure_ascii=False, indent=2
        ).encode("utf-8")
        media_type = GEOJSON_MEDIA_TYPE
    elif fmt == "zip":
        content, media_type = to_zip(plan), ZIP_MEDIA_TYPE
    else:
        raise ExportError(f"неизвестный формат выгрузки «{fmt}»")

    filename = filename_for(plan, uav_id, fmt)
    repositories.plans.record_export(plan.id, uav_id, fmt, filename, user)
    return content, filename, media_type
