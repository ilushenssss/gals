"""Тесты KML-загрузки обстановки: uav_planner.kml (см. модуль ``environment.py``
про то, какие виды KML распознаются и какие v1-упрощения приняты) и
интеграция через POST /api/environments — те же файлы, что даны в кейсе
(docs/examples), проверены отдельно вручную (см. README), здесь —
небольшие синтетические фрагменты тех же самых форматов."""

import io

from uav_planner.kml.altitude_text import UNBOUNDED_CEILING_M, parse_altitude_range
from uav_planner.kml.environment import UAV_CEILING_M, kml_to_environment_geojson
from uav_planner.kml.parser import _parse_coordinates, parse_placemarks


def _square_ring(x0, y0, size):
    return f"{x0},{y0} {x0+size},{y0} {x0+size},{y0+size} {x0},{y0+size} {x0},{y0}"


def _no_fly_placemark(name, altitudes_text, geom_id=1):
    return f"""
      <Placemark>
        <name>{name}</name>
        <description>{altitudes_text}</description>
        <ExtendedData>
          <Data name="Name"><value>{name}</value></Data>
          <Data name="Altitudes"><value>{altitudes_text}</value></Data>
          <Data name="Type"><value>врем_ограничение</value></Data>
        </ExtendedData>
        <MultiGeometry>
          <Polygon>
            <outerBoundaryIs>
              <LinearRing>
                <coordinates>{_square_ring(37.0 + geom_id, 55.0, 0.05)}</coordinates>
              </LinearRing>
            </outerBoundaryIs>
          </Polygon>
        </MultiGeometry>
      </Placemark>
    """


def _obstacle_polygon_placemark(name, z, altitude_mode="relativeToGround", geom_id=1):
    ring = _square_ring(37.0 + geom_id, 55.0, 0.001)
    coords = " ".join(f"{pair},{z}" for pair in ring.split())
    return f"""
      <Placemark>
        <name>{name}</name>
        <Polygon>
          <extrude>1</extrude>
          <altitudeMode>{altitude_mode}</altitudeMode>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>{coords}</coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
      </Placemark>
    """


def _obstacle_linestring_placemark(name, z, geom_id=1):
    return f"""
      <Placemark>
        <name>{name}</name>
        <LineString>
          <extrude>1</extrude>
          <altitudeMode>relativeToGround</altitudeMode>
          <coordinates>{37.0+geom_id},55.0,{z} {37.001+geom_id},55.001,{z}</coordinates>
        </LineString>
      </Placemark>
    """


def _kml_document(*placemarks_xml):
    body = "".join(placemarks_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>{body}</Document></kml>""".encode("utf-8")


# ---------- altitude_text.parse_altitude_range ----------

def test_parses_from_ground_to_meters():
    assert parse_altitude_range("От земли до 300 м (1000 фут) AMSL") == (0.0, 300.0)


def test_parses_flight_levels():
    lo, hi = parse_altitude_range("От FL280 до FL400")
    assert lo == 280 * 100 * 0.3048
    assert hi == 400 * 100 * 0.3048


def test_parses_meters_to_flight_level():
    lo, hi = parse_altitude_range("От 800 м (2700 фут) AMSL до FL90")
    assert lo == 800.0
    assert hi == 90 * 100 * 0.3048


def test_parses_unbounded():
    assert parse_altitude_range("На всех высотах") == (0.0, UNBOUNDED_CEILING_M)


def test_ignores_trailing_free_text_after_first_line():
    lo, hi = parse_altitude_range("От земли до FL260\nИсключая границы района аэродрома Липецк")
    assert lo == 0.0
    assert hi == 260 * 100 * 0.3048


def test_unparseable_text_returns_none():
    assert parse_altitude_range("SVO") is None
    assert parse_altitude_range("") is None
    assert parse_altitude_range(None) is None


# ---------- parser._parse_coordinates ----------

def test_parses_coordinates_with_space_after_comma():
    # Реальный дефект в "obstacles_Московская область.kml": один Placemark
    # содержит вырожденную координату "0.0, 0.0, 0.0" с пробелом после
    # запятой, а не привычный "0.0,0.0,0.0" — наивное разбиение по пробелам
    # рвет один триплет на три токена и падает на float('').
    assert _parse_coordinates("0.0, 0.0, 0.0") == [(0.0, 0.0, 0.0)]


def test_parses_normal_comma_separated_coordinates():
    assert _parse_coordinates("35.6,56.4,77.0 35.7,56.5,77.0") == [
        (35.6, 56.4, 77.0), (35.7, 56.5, 77.0),
    ]


# ---------- parser.parse_placemarks ----------

def test_parses_multigeometry_wrapped_polygon_with_extended_data():
    kml = _kml_document(_no_fly_placemark("UUR201", "От земли до 300 м"))
    placemarks = parse_placemarks(kml)
    assert len(placemarks) == 1
    p = placemarks[0]
    assert p.name == "UUR201"
    assert p.extended_data["Type"] == "врем_ограничение"
    assert len(p.geometries) == 1
    assert p.geometries[0].kind == "Polygon"
    assert len(p.geometries[0].exterior) == 5  # квадрат, замкнутое кольцо


def test_parses_extruded_polygon_with_z():
    kml = _kml_document(_obstacle_polygon_placemark("TOWER-1", z=77.5))
    placemarks = parse_placemarks(kml)
    geom = placemarks[0].geometries[0]
    assert geom.extrude is True
    assert geom.altitude_mode == "relativeToGround"
    assert all(z == 77.5 for _x, _y, z in geom.exterior)


# ---------- environment.kml_to_environment_geojson ----------

def test_no_fly_zone_below_ceiling_is_imported():
    kml = _kml_document(_no_fly_placemark("LOW", "От земли до 300 м"))
    geojson = kml_to_environment_geojson(kml)
    assert len(geojson["features"]) == 1
    f = geojson["features"][0]
    assert f["properties"]["layer"] == "no_fly"
    assert f["properties"]["name"] == "LOW"
    assert "_source_flagged_invalid" not in f["properties"]


def test_no_fly_zone_entirely_above_uav_ceiling_is_excluded():
    assert UAV_CEILING_M == 500.0
    kml = _kml_document(_no_fly_placemark("HIGH", "От FL280 до FL400", geom_id=1))
    try:
        kml_to_environment_geojson(kml)
        assert False, "должно было закончиться ошибкой — ни одного объекта не распознано"
    except ValueError as exc:
        assert "не найдено" in str(exc)


def test_no_fly_zone_unparseable_altitude_is_imported_conservatively():
    # Неразобранная высота -> считаем, что зона может начинаться от земли,
    # и все равно импортируем (консервативно), см. docstring environment.py.
    kml = _kml_document(_no_fly_placemark("SVO", "SVO"))
    geojson = kml_to_environment_geojson(kml)
    assert len(geojson["features"]) == 1


def test_source_flagged_invalid_geometry_is_imported_and_flagged():
    text = "Некорректная геометрия! От 800 м (2700 фут) AMSL до FL90"
    kml = _kml_document(_no_fly_placemark("UUR201", text))
    geojson = kml_to_environment_geojson(kml)
    assert len(geojson["features"]) == 1
    props = geojson["features"][0]["properties"]
    assert props["_source_flagged_invalid"].startswith("Некорректная геометрия")


def test_obstacle_polygon_height_from_z_relative_to_ground():
    kml = _kml_document(_obstacle_polygon_placemark("TOWER-1", z=77.5))
    geojson = kml_to_environment_geojson(kml)
    f = geojson["features"][0]
    assert f["properties"]["layer"] == "obstacle"
    assert f["properties"]["h_min"] == 0.0
    assert f["properties"]["h_max"] == 77.5


def test_obstacle_polygon_absolute_altitude_mode_still_uses_z_as_height():
    # v1-упрощение: altitudeMode игнорируется, Z всегда высота над землей
    # (см. docstring kml.environment) — по решению пользователя при уточнении.
    kml = _kml_document(_obstacle_polygon_placemark("TOWER-2", z=200.0, altitude_mode="absolute"))
    geojson = kml_to_environment_geojson(kml)
    assert geojson["features"][0]["properties"]["h_max"] == 200.0


def test_obstacle_linestring_becomes_buffered_polygon():
    kml = _kml_document(_obstacle_linestring_placemark("POWERLINE-1", z=45.0))
    geojson = kml_to_environment_geojson(kml)
    f = geojson["features"][0]
    assert f["properties"]["layer"] == "obstacle"
    assert f["properties"]["h_max"] == 45.0
    assert f["geometry"]["type"] == "Polygon"


def test_mixed_file_imports_both_kinds():
    kml = _kml_document(
        _no_fly_placemark("LOW", "От земли до 300 м", geom_id=1),
        _obstacle_polygon_placemark("TOWER-1", z=77.5, geom_id=2),
    )
    geojson = kml_to_environment_geojson(kml)
    layers = sorted(f["properties"]["layer"] for f in geojson["features"])
    assert layers == ["no_fly", "obstacle"]


def test_unrecognized_kml_raises_value_error():
    kml = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">
    <Document><Placemark><name>plain</name></Placemark></Document></kml>"""
    try:
        kml_to_environment_geojson(kml)
        assert False, "ожидалась ValueError"
    except ValueError:
        pass


# ---------- интеграция: POST /api/environments с KML-файлом ----------

def test_upload_kml_environment_end_to_end(client):
    kml = _kml_document(
        _no_fly_placemark("LOW", "От земли до 300 м", geom_id=1),
        _obstacle_polygon_placemark("TOWER-1", z=77.5, geom_id=2),
    )
    resp = client.post(
        "/api/environments",
        data={"name": "KML-обстановка"},
        files={"file": ("scene.kml", io.BytesIO(kml), "application/vnd.google-earth.kml+xml")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Корректна"
    assert body["counts"]["no_fly"] == 1
    assert body["counts"]["obstacle"] == 1


def test_upload_kml_with_flagged_invalid_geometry_reports_error(client):
    text = "Некорректная геометрия! От земли до 300 м"
    kml = _kml_document(_no_fly_placemark("UUR201", text))
    resp = client.post(
        "/api/environments",
        data={"name": "KML с ошибкой"},
        files={"file": ("scene.kml", io.BytesIO(kml), "application/vnd.google-earth.kml+xml")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("некорректн" in e["message"] for e in body["errors"])


def test_upload_kml_with_nothing_recognized_returns_400(client):
    kml = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">
    <Document><Placemark><name>plain</name></Placemark></Document></kml>"""
    resp = client.post(
        "/api/environments",
        data={"name": "Пустой KML"},
        files={"file": ("scene.kml", io.BytesIO(kml), "application/vnd.google-earth.kml+xml")},
    )
    assert resp.status_code == 400
    assert "kml" in resp.json()["detail"].lower()
