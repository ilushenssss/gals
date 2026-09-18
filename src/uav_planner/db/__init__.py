"""Слой доступа к БД: движок, сессии, базовый класс моделей, гео-конверсия."""

from .base import Base, TimestampMixin, UuidPkMixin, utcnow
from .geo import (
    SRID_WGS84,
    GeoConversionError,
    from_db,
    from_db_geojson,
    geojson_to_shape,
    to_db,
    to_db_multiline,
    to_db_multipolygon,
)
from .session import (
    NoSessionError,
    bind_session,
    current_session,
    get_engine,
    get_session,
    get_session_factory,
    has_session,
    reset_engine,
    session_scope,
    set_session_factory,
    unbind_session,
)

__all__ = [
    "Base", "TimestampMixin", "UuidPkMixin", "utcnow",
    "SRID_WGS84", "GeoConversionError", "from_db", "from_db_geojson",
    "geojson_to_shape", "to_db", "to_db_multiline", "to_db_multipolygon",
    "get_engine", "get_session", "get_session_factory", "reset_engine",
    "session_scope", "set_session_factory", "current_session", "bind_session",
    "unbind_session", "has_session", "NoSessionError",
]
