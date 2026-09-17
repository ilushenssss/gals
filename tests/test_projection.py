import math

import pytest
from shapely.geometry import Point, Polygon

from uav_planner.geometry import Projector, utm_crs_for


def test_utm_zone_moscow_north():
    # Москва: lon ~37.62, lat ~55.75 -> зона 37, север.
    crs = utm_crs_for(37.62, 55.75)
    assert crs.to_epsg() == 32637


def test_utm_zone_south_hemisphere():
    # Буэнос-Айрес: lon ~-58.4, lat ~-34.6 -> зона 21, юг.
    crs = utm_crs_for(-58.4, -34.6)
    assert crs.to_epsg() == 32721


def test_utm_zone_bounds():
    assert utm_crs_for(-180.0, 10.0).to_epsg() == 32601
    assert utm_crs_for(179.99, 10.0).to_epsg() == 32660


def test_roundtrip_point_close_to_original():
    lon, lat = 37.62, 55.75
    projector = Projector.for_point(lon, lat)
    point = Point(lon, lat)

    projected = projector.to_utm(point)
    back = projector.to_wgs84(projected)

    assert back.x == pytest.approx(lon, abs=1e-9)
    assert back.y == pytest.approx(lat, abs=1e-9)


def test_projected_distance_matches_expected_meters():
    # Два меридиана широты 55.75, разница по долготе ~0.01347 град
    # соответствует ~837 м на этой широте (проверка на разумную точность UTM).
    lon0, lat0 = 37.60, 55.75
    lon1, lat1 = 37.60 + 0.01347, 55.75

    projector = Projector.for_point(lon0, lat0)
    p0 = projector.to_utm(Point(lon0, lat0))
    p1 = projector.to_utm(Point(lon1, lat1))

    distance = p0.distance(p1)
    expected = 0.01347 * math.radians(1) * 6371000 * math.cos(math.radians(lat0))
    assert distance == pytest.approx(expected, rel=0.01)


def test_for_geometry_uses_centroid():
    polygon = Polygon([(37.5, 55.7), (37.7, 55.7), (37.7, 55.8), (37.5, 55.8)])
    projector = Projector.for_geometry(polygon)
    assert projector.utm_crs.to_epsg() == utm_crs_for(37.6, 55.75).to_epsg()


def test_area_preserved_reasonably_in_utm():
    # Квадрат ~100х100 м в проекции WGS-84 (малый), проверяем что площадь в UTM
    # близка к ожидаемой (не искажена ошибкой перестановки осей и т.п.).
    lon0, lat0 = 37.60, 55.75
    d_lon = 100.0 / (111320.0 * math.cos(math.radians(lat0)))
    d_lat = 100.0 / 110540.0
    polygon = Polygon(
        [
            (lon0, lat0),
            (lon0 + d_lon, lat0),
            (lon0 + d_lon, lat0 + d_lat),
            (lon0, lat0 + d_lat),
        ]
    )
    projector = Projector.for_geometry(polygon)
    utm_polygon = projector.to_utm(polygon)
    assert utm_polygon.area == pytest.approx(100.0 * 100.0, rel=0.02)
