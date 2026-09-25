"""Проверка безопасности сверяет зоны с фактическим вылетом: диапазон высот
разрешенного пространства и интервалы действия зон (регрессии аудита,
docs/AUDIT.md). План собирается вручную — расчетное ядро такой план по
построению не выдаст, а проверка обязана поймать его независимо."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from uav_planner.services import safety_service

START = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


def _square(x0, y0, w, h):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h], [x0, y0]]]}


def _feature(layer, geometry, **props):
    return {"type": "Feature", "properties": {"layer": layer, **props}, "geometry": geometry}


def _checks(airspace, no_fly=()):
    route = {"type": "LineString", "coordinates": [[37.575, 55.704], [37.59, 55.704], [37.575, 55.704]]}
    env = SimpleNamespace(layers={
        "airspace": list(airspace),
        "no_fly": list(no_fly),
        "obstacle": [],
        "launch_site": [_feature("launch_site", {"type": "Point", "coordinates": [37.575, 55.704]})],
        "reserve_site": [],
    })
    task = SimpleNamespace(
        area=_square(37.58, 55.702, 0.005, 0.004), window_start=None, window_end=None, timezone=None,
    )
    sortie = SimpleNamespace(
        uav_id="A", sortie_index=0, track_geojson=route,
        survey_tracks_geojson={"type": "MultiLineString", "coordinates": [route["coordinates"][:2]]},
        start_utc=START, end_utc=END,
    )
    plan = SimpleNamespace(height_m=100.0, cruise_speed_mps=10.0, budget_s=3600.0, swath_m=100.0, sorties=[sortie])
    return {c.name: c for c in safety_service._run_checks(env, task, plan)}


WIDE = _square(37.55, 55.70, 0.10, 0.01)


def test_airspace_zone_below_flight_height_does_not_allow_the_flight():
    # Регрессия: зоны объединялись без учета высоты — полет на 100 м в зоне
    # с потолком 50 м считался разрешенным.
    checks = _checks([_feature("airspace", WIDE, h_min=0, h_max=50)])
    assert not checks["airspace"].passed
    assert "на высоте 100 м" in checks["airspace"].violations[0].message

    assert _checks([_feature("airspace", WIDE, h_min=0, h_max=300)])["airspace"].passed


def test_airspace_zone_must_be_active_for_the_whole_sortie():
    half = [{"start": START.isoformat(), "end": (START + timedelta(minutes=5)).isoformat()}]
    whole = [{"start": (START - timedelta(hours=1)).isoformat(), "end": (END + timedelta(hours=1)).isoformat()}]
    assert not _checks([_feature("airspace", WIDE, h_min=0, h_max=300, active_windows=half)])["airspace"].passed
    assert _checks([_feature("airspace", WIDE, h_min=0, h_max=300, active_windows=whole)])["airspace"].passed


def test_no_fly_zone_counts_only_while_active():
    airspace = [_feature("airspace", WIDE, h_min=0, h_max=300)]
    zone = _square(37.584, 55.7035, 0.001, 0.001)  # поперек маршрута
    later = [{"start": (END + timedelta(hours=1)).isoformat(), "end": (END + timedelta(hours=2)).isoformat()}]
    during = [{"start": (START + timedelta(minutes=9)).isoformat(), "end": (END + timedelta(hours=2)).isoformat()}]

    assert not _checks(airspace, [_feature("no_fly", zone)])["geozones"].passed
    assert _checks(airspace, [_feature("no_fly", zone, active_windows=later)])["geozones"].passed
    assert not _checks(airspace, [_feature("no_fly", zone, active_windows=during)])["geozones"].passed
