"""Разбор KML в общие структуры — без стороннего пакета (``fastkml``/``pykml``):
нужен небольшой, предсказуемый набор тегов (``Placemark``, ``Polygon``,
``LineString``, ``MultiGeometry``, ``ExtendedData``), а свободный текст высот
в реальных файлах кейса все равно требует своего парсера (см.
``kml.altitude_text``) — общий пакет не снял бы эту работу, только добавил
бы зависимость.

Пространство имен KML не учитывается по имени (сравниваются только локальные
имена тегов, ``{...}Placemark`` -> ``Placemark``) — так парсер работает и на
файлах с ``xmlns="http://www.opengis.net/kml/2.2"``, и на гипотетических
файлах без объявленного пространства имен.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

Point3 = tuple[float, float, float | None]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _find_all(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if _local(c.tag) == name]


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    value = elem.text.strip()
    return value or None


_COMMA_SPACE_RE = re.compile(r",\s+")


def _parse_coordinates(text: str) -> list[Point3]:
    """KML определяет координаты как разделенные пробелом триплеты,
    разделенные запятой без пробелов, — но в реальных файлах кейса
    встречается запись с пробелом после запятой (например, "0.0, 0.0, 0.0"),
    из-за которой наивное разбиение по пробелам рвет один триплет на куски.
    Пробел после запятой убирается заранее, независимо от того, есть ли он
    в конкретном файле."""
    normalized = _COMMA_SPACE_RE.sub(",", text)
    points: list[Point3] = []
    for token in normalized.split():
        parts = token.split(",")
        if len(parts) < 2 or parts[0] == "" or parts[1] == "":
            continue
        lon, lat = float(parts[0]), float(parts[1])
        z = float(parts[2]) if len(parts) > 2 and parts[2] not in ("", None) else None
        points.append((lon, lat, z))
    return points


def _parse_linear_ring(boundary_elem: ET.Element) -> list[Point3]:
    ring = _find(boundary_elem, "LinearRing")
    if ring is None:
        return []
    coords = _find(ring, "coordinates")
    return _parse_coordinates(coords.text or "") if coords is not None and coords.text else []


@dataclass
class KmlGeometry:
    """Одна геометрия ``Placemark`` — полигон (с возможными дырами) или линия.

    Высота (``z`` в ``exterior``/``holes``) — как записана в файле, без
    интерпретации ``altitude_mode``: что она означает физически, решает
    вызывающий код (см. ``kml.environment``), а не парсер.
    """

    kind: str  # "Polygon" | "LineString"
    exterior: list[Point3]
    holes: list[list[Point3]] = field(default_factory=list)
    altitude_mode: str | None = None
    extrude: bool = False


def _parse_extrude_and_mode(elem: ET.Element) -> tuple[bool, str | None]:
    extrude_el = _find(elem, "extrude")
    extrude = bool(extrude_el is not None and (extrude_el.text or "").strip() == "1")
    mode = _text(_find(elem, "altitudeMode"))
    return extrude, mode


def _parse_polygon(elem: ET.Element) -> KmlGeometry:
    extrude, mode = _parse_extrude_and_mode(elem)
    outer = _find(elem, "outerBoundaryIs")
    exterior = _parse_linear_ring(outer) if outer is not None else []
    holes = [_parse_linear_ring(inner) for inner in _find_all(elem, "innerBoundaryIs")]
    return KmlGeometry(kind="Polygon", exterior=exterior, holes=holes, altitude_mode=mode, extrude=extrude)


def _parse_linestring(elem: ET.Element) -> KmlGeometry:
    extrude, mode = _parse_extrude_and_mode(elem)
    coords = _find(elem, "coordinates")
    points = _parse_coordinates(coords.text or "") if coords is not None and coords.text else []
    return KmlGeometry(kind="LineString", exterior=points, altitude_mode=mode, extrude=extrude)


def _collect_geometries(elem: ET.Element) -> list[KmlGeometry]:
    """Собирает ``Polygon``/``LineString`` прямо под элементом и внутри
    произвольно вложенных ``MultiGeometry`` (см. "Московская зона.kml", где
    каждый полигон обернут в ``MultiGeometry`` из одного элемента)."""
    geoms: list[KmlGeometry] = []
    for child in elem:
        local = _local(child.tag)
        if local == "Polygon":
            geoms.append(_parse_polygon(child))
        elif local == "LineString":
            geoms.append(_parse_linestring(child))
        elif local == "MultiGeometry":
            geoms.extend(_collect_geometries(child))
    return geoms


def _parse_extended_data(elem: ET.Element) -> dict[str, str]:
    data: dict[str, str] = {}
    ext = _find(elem, "ExtendedData")
    if ext is None:
        return data
    for entry in _find_all(ext, "Data"):
        name = entry.get("name")
        value = _text(_find(entry, "value"))
        if name and value is not None:
            data[name] = value
    return data


@dataclass
class KmlPlacemark:
    name: str | None
    description: str | None
    extended_data: dict[str, str]
    geometries: list[KmlGeometry]


def parse_placemarks(raw: bytes) -> list[KmlPlacemark]:
    """Все ``Placemark`` документа, в порядке появления в файле."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"файл не является корректным KML/XML: {exc}") from exc

    placemarks: list[KmlPlacemark] = []
    for elem in root.iter():
        if _local(elem.tag) != "Placemark":
            continue
        placemarks.append(
            KmlPlacemark(
                name=_text(_find(elem, "name")),
                description=_text(_find(elem, "description")),
                extended_data=_parse_extended_data(elem),
                geometries=_collect_geometries(elem),
            )
        )
    return placemarks
