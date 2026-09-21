"""Проекция WGS-84 <-> UTM.

Вход и выход сервиса — WGS-84 (в GeoJSON порядок координат: долгота, широта).
Все расчеты длин, площадей и буферов ведутся в метрической проекции UTM —
зона выбирается по центроиду сцены. Тестовые сценарии ограничены 100 км²
(см. ответы экспертов), поэтому сцена не пересекает границу соседней зоны UTM
и одной проекции для всей сцены достаточно.
"""

from __future__ import annotations

from pyproj import CRS, Geod, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

WGS84 = CRS.from_epsg(4326)
_GEOD = Geod(ellps="WGS84")


def geodesic_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Расстояние по эллипсоиду WGS-84 между двумя точками (метры) — для
    сравнений на масштабе сотен-тысяч км (например, парк БВС и обстановка в
    разных городах), где локальная UTM-проекция ``Projector`` уже не подходит
    (действительна только вблизи своей зоны)."""
    _, _, distance_m = _GEOD.inv(lon1, lat1, lon2, lat2)
    return distance_m


def utm_crs_for(lon: float, lat: float) -> CRS:
    """CRS UTM (WGS-84 / UTM zone N или S) для точки с заданными lon/lat."""
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


class Projector:
    """Переводит геометрии между WGS-84 и метрической проекцией UTM.

    Координаты в обеих системах — (x, y) = (lon, lat) или (easting, northing),
    т.е. без перестановки осей (``always_xy=True``), как принято в GeoJSON.
    """

    def __init__(self, utm_crs: CRS):
        self.utm_crs = utm_crs
        self._to_utm = Transformer.from_crs(WGS84, utm_crs, always_xy=True)
        self._to_wgs84 = Transformer.from_crs(utm_crs, WGS84, always_xy=True)

    @classmethod
    def for_point(cls, lon: float, lat: float) -> "Projector":
        return cls(utm_crs_for(lon, lat))

    @classmethod
    def for_geometry(cls, geom: BaseGeometry) -> "Projector":
        """Подбирает зону UTM по центроиду геометрии в WGS-84."""
        centroid = geom.centroid
        return cls.for_point(centroid.x, centroid.y)

    def to_utm(self, geom: BaseGeometry) -> BaseGeometry:
        """WGS-84 -> UTM (метры)."""
        return shapely_transform(self._to_utm.transform, geom)

    def to_wgs84(self, geom: BaseGeometry) -> BaseGeometry:
        """UTM (метры) -> WGS-84."""
        return shapely_transform(self._to_wgs84.transform, geom)
