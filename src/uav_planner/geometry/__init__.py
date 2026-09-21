from .errors import GeometryError
from .projection import Projector, geodesic_distance_m, utm_crs_for
from .area import (
    HeightRange,
    TimeWindow,
    AllowedZone,
    NoFlyZone,
    Obstacle,
    compute_working_area,
    validate_polygon,
    safe_simplify,
)

__all__ = [
    "GeometryError",
    "Projector",
    "geodesic_distance_m",
    "utm_crs_for",
    "HeightRange",
    "TimeWindow",
    "AllowedZone",
    "NoFlyZone",
    "Obstacle",
    "compute_working_area",
    "validate_polygon",
    "safe_simplify",
]
