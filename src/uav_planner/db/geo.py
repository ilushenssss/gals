"""Преобразование shapely <-> PostGIS.

Единственное место, где геометрия пересекает границу БД. Собрано в одном
модуле, потому что здесь четыре ловушки, каждая из которых молча портит данные:

1. SRID нужно проставлять явно, иначе PostGIS пишет SRID 0 и typmod колонки
   ``geometry(..., 4326)`` отвергает строку;
2. читать обратно нужно через ``to_shape`` (WKB, точные IEEE754), а не через
   ``ST_AsGeoJSON``, который режет координаты до 9 знаков — иначе координаты
   плана отличаются между записью и чтением;
3. колонку ``MultiLineString`` нельзя заполнить одиночным ``LineString``;
4. пустые геометрии через WKB ходят плохо — не персистим их вовсе.
"""

from __future__ import annotations

from typing import Any

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiLineString, MultiPolygon, mapping, shape
from shapely.geometry.base import BaseGeometry

SRID_WGS84 = 4326


class GeoConversionError(ValueError):
    """Геометрию нельзя сохранить в БД в том виде, в каком она пришла."""


def to_db(geom: BaseGeometry | None) -> WKBElement | None:
    if geom is None:
        return None
    if geom.is_empty:
        raise GeoConversionError("пустая геометрия не сохраняется")
    return from_shape(geom, srid=SRID_WGS84)


def to_db_multiline(geom: BaseGeometry | None) -> WKBElement | None:
    """Защитно приводит LineString к MultiLineString под typmod колонки."""
    if geom is None:
        return None
    if geom.geom_type == "LineString":
        geom = MultiLineString([geom])
    return to_db(geom)


def to_db_multipolygon(geom: BaseGeometry | None) -> WKBElement | None:
    if geom is None:
        return None
    if geom.geom_type == "Polygon":
        geom = MultiPolygon([geom])
    return to_db(geom)


def from_db(value: WKBElement | None) -> BaseGeometry | None:
    if value is None:
        return None
    return to_shape(value)


def from_db_geojson(value: WKBElement | None) -> dict[str, Any] | None:
    """GeoJSON-геометрия для ответа API — через shapely, не через ST_AsGeoJSON."""
    geom = from_db(value)
    return None if geom is None else mapping(geom)


def geojson_to_shape(geojson: dict[str, Any]) -> BaseGeometry:
    return shape(geojson)
