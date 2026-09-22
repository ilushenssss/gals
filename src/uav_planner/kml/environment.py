"""KML -> GeoJSON для модуля «Обстановка» (см. docs/trebovania/Обстановка.md).

Поддержаны два вида KML — ровно те, с которыми реально пришли примерные
файлы кейса (docs/examples, см. "Описание файлов.docx"):

  - **зоны ограничений/запретов** ("Московская зона.kml"): ``Placemark`` с
    ``ExtendedData`` {``Name``, ``Altitudes``, ``Type``}; высотный диапазон —
    свободный текст без стандартного формата (см. ``kml.altitude_text``).
    Ложится на слой ``no_fly``.
  - **высотные препятствия** ("obstacles_*.kml", "высотные препятствия
    *.kml"): ``Placemark`` с экструдированным ``Polygon``/``LineString``
    (``extrude=1``); высота — Z-координата вершин. Ложится на слой
    ``obstacle``.

Другие виды KML (например, "Границы полетов.kml" — набор полигонов без
названий, типов и высот, то есть, по смыслу, пример области облета для
модуля «Задача», а не обстановки) не распознаются этим модулем и не дают ни
одного объекта — сознательно, а не как недосмотр.

**v1-упрощение №1 (высота препятствия).** ``altitudeMode`` игнорируется: Z
всегда трактуется как высота НАД ЗЕМЛЕЙ, а не как ``relativeToGround``
предписывает стандарт KML для ``absolute``/``relativeToSeaFloor``. Часть
примеров (около половины в обоих файлах препятствий) размечена именно так —
Z там означает высоту над уровнем моря, а не высоту самого препятствия. Без
модели рельефа (которой у сервиса нет и не планируется для v1) отделить
высоту препятствия от высоты подстилающей поверхности невозможно. Взятое
допущение — трактовать Z как высоту препятствия целиком — систематически
завышает эту высоту (на высоту рельефа в точке), то есть ошибается в
консервативную, безопасную сторону: препятствие может быть учтено выше, чем
оно есть, но не наоборот. Обратное допущение (считать такие препятствия
нулевой высоты) было бы честнее с точки зрения "чего мы не знаем", но опаснее
с точки зрения планирования полета, поэтому выбор сделан в пользу первого.

**v1-упрощение №2 (время действия ограничения).** Часть зон в "Московская
зона.kml" размечена как "врем_ограничение" (временное), но условие
активации — свободный текст вида "в период выполнения полетов в районе
аэродрома Липецк" — зависит от чужой активности, а не от календарной даты
или времени суток, и не сводится к ``TimeWindow`` (``start``/``end``
ISO8601), которую понимает ``uav_planner.geometry.TimeWindow``. Такие зоны
импортируются как действующие всегда (``active_windows`` не заполняется) —
это тоже консервативный выбор: зона, которая на самом деле активна не всегда,
будет учтена как действующая всегда, а не наоборот.

**v1-сужение (высотный диапазон и релевантность BVS).** У слоя ``no_fly`` в
нашей модели нет диапазона высот (``NoFlyZone`` — это столб на всех высотах,
см. ``uav_planner.geometry.NoFlyZone``) — большинство реальных зон
ограничений в файле актуальны только для больших эшелонов (например,
FL280-FL400, десятки тысяч футов), до которых БВС физически не долетит.
Импортировать их как обычный ``no_fly`` (без учета высоты) означало бы
заблокировать под ними всю площадь для БВС без всякой причины — файл почти
целиком превратился бы в "здесь летать нельзя". Поэтому распознанный
высотный диапазон используется не для сохранения (у ``no_fly`` его негде
хранить), а для отбора: зона импортируется, только если её нижняя граница
ниже ``UAV_CEILING_M`` (500 м — практический потолок парка, см.
``uav_planner.fleet.FLEET_MODELS``, потолок Geoscan Gemini/801); зоны,
целиком выше этого потолка, для БВС-планирования не релевантны и не
импортируются вовсе (не как ошибка — как то, что вне области действия этого
сервиса). Если диапазон не разобрался (текст не подошел под
``kml.altitude_text``), зона все равно импортируется — консервативно
считаем, что она может начинаться от земли, раз не знаем точно.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import LineString, Polygon, mapping
from shapely.geometry.base import BaseGeometry

from uav_planner.geometry import Projector

from .altitude_text import parse_altitude_range
from .parser import KmlGeometry, KmlPlacemark, parse_placemarks

# Так авторы примерных данных сами помечают заведомо проблемные объекты (см.
# "Московская зона.kml", например, UUR201, UUR244). Проверяется явно, а не
# только через невалидность геометрии по shapely: тонкое самопересечение
# shapely может не заметить, а автор данных знает точно.
_SOURCE_INVALID_MARKER = "Некорректная геометрия"

# Практический потолок парка (Geoscan Gemini/801, см. FLEET_MODELS) — порог
# отбора зон ограничений по высоте, см. docstring модуля выше.
UAV_CEILING_M = 500.0

# Ширина "коридора" для препятствий, заданных линией, а не полигоном
# (например, пролет линии электропередачи, см. категорию
# POWER_TRANSMISSION_PYLON) — наша модель ждет полигон-footprint для каждого
# препятствия, поэтому линия оборачивается в тонкий буфер.
_LINE_OBSTACLE_BUFFER_M = 1.0


def _ring_xy(points: list[tuple[float, float, float | None]]) -> list[tuple[float, float]]:
    return [(x, y) for x, y, _z in points]


def _max_z(*rings: list[tuple[float, float, float | None]]) -> float:
    zs = [z for ring in rings for _x, _y, z in ring if z is not None]
    return max(zs) if zs else 0.0


def _polygon_from_geometry(geom: KmlGeometry) -> BaseGeometry | None:
    if len(geom.exterior) < 3:
        return None
    holes = [_ring_xy(h) for h in geom.holes if len(h) >= 3]
    try:
        return Polygon(_ring_xy(geom.exterior), holes)
    except ValueError:
        return None


def _obstacle_feature(placemark: KmlPlacemark, geom: KmlGeometry, projector: Projector | None) -> dict[str, Any] | None:
    if geom.kind == "Polygon":
        polygon = _polygon_from_geometry(geom)
        if polygon is None:
            return None
        h_max = _max_z(geom.exterior, *geom.holes)
    else:
        if len(geom.exterior) < 2 or projector is None:
            return None
        line = LineString(_ring_xy(geom.exterior))
        polygon = projector.to_wgs84(projector.to_utm(line).buffer(_LINE_OBSTACLE_BUFFER_M))
        h_max = _max_z(geom.exterior)

    return {
        "type": "Feature",
        "properties": {"layer": "obstacle", "name": placemark.name, "h_min": 0.0, "h_max": h_max},
        "geometry": mapping(polygon),
    }


def _no_fly_feature(placemark: KmlPlacemark, geom: KmlGeometry) -> dict[str, Any] | None:
    polygon = _polygon_from_geometry(geom)
    if polygon is None:
        return None

    name = placemark.name or placemark.extended_data.get("Name")
    altitudes_text = placemark.extended_data.get("Altitudes") or placemark.description or ""

    if _SOURCE_INVALID_MARKER in altitudes_text:
        first_line = altitudes_text.strip().splitlines()[0][:300]
        return {
            "type": "Feature",
            "properties": {"layer": "no_fly", "name": name, "_source_flagged_invalid": first_line},
            "geometry": mapping(polygon),
        }

    parsed = parse_altitude_range(altitudes_text)
    h_min = parsed[0] if parsed is not None else 0.0
    if h_min >= UAV_CEILING_M:
        return None  # выше практического потолка БВС — вне области действия сервиса, см. docstring

    return {
        "type": "Feature",
        "properties": {"layer": "no_fly", "name": name},
        "geometry": mapping(polygon),
    }


def kml_to_environment_geojson(raw: bytes) -> dict[str, Any]:
    """Разбирает KML и строит GeoJSON FeatureCollection в формате, который
    принимает ``environment_service.validate_and_store`` — дальше файл идет
    по тому же самому пути валидации и хранения, что и обычный GeoJSON."""
    placemarks = parse_placemarks(raw)

    obstacle_lines = [
        g for p in placemarks for g in p.geometries if g.extrude and g.kind == "LineString" and g.exterior
    ]
    projector = None
    if obstacle_lines:
        lon, lat, _ = obstacle_lines[0].exterior[0]
        projector = Projector.for_point(lon, lat)

    features: list[dict[str, Any]] = []
    for placemark in placemarks:
        is_restriction_zone = "Type" in placemark.extended_data or "Altitudes" in placemark.extended_data
        for geom in placemark.geometries:
            if geom.extrude:
                feature = _obstacle_feature(placemark, geom, projector)
            elif is_restriction_zone and geom.kind == "Polygon":
                feature = _no_fly_feature(placemark, geom)
            else:
                feature = None
            if feature is not None:
                features.append(feature)

    if not features:
        raise ValueError(
            "не найдено ни одной распознанной зоны ограничения (ExtendedData "
            "Name/Altitudes/Type) или экструдированного препятствия "
            "(extrude=1, Polygon/LineString) — поддержаны только эти два вида KML"
        )
    return {"type": "FeatureCollection", "features": features}
