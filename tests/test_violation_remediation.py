"""Исправление нарушений (БЕЗ.ФТ.3–4): рекомендация максимально допустимого
GSD при превышении потолка высоты и автопересчет с расширенным буфером вокруг
запретных зон."""

import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from shapely.geometry import shape

from test_api_plan import _create_task, _upload_fleet, square_coords
from test_api_audit_fixes import _plan, _upload_environment

from uav_planner.camera import CAMERA_SPECS, CameraError, max_gsd_for_height, plan_survey_geometry, survey_height
from uav_planner.geometry import Projector
from uav_planner.services import plan_service, safety_service


# ---------- максимально допустимое GSD ----------

@pytest.mark.parametrize("camera_key", sorted(CAMERA_SPECS))
def test_max_gsd_is_the_largest_value_within_ceiling(camera_key):
    camera = CAMERA_SPECS[camera_key]
    gsd = max_gsd_for_height(camera, 150.0)
    assert survey_height(camera, gsd) <= 150.0
    assert survey_height(camera, round(gsd + 0.1, 1)) > 150.0  # шаг 0.1 см вверх уже выше потолка


def test_plan_rejection_names_max_allowed_gsd():
    # PF1B: GSD 3.0 см -> 153.2 м. Раньше сообщение советовало «более крупный
    # GSD», то есть поднять высоту еще выше.
    with pytest.raises(CameraError) as exc:
        plan_survey_geometry("geoscan-gemini", "pf1b", 3.0)
    message = str(exc.value)
    assert "уменьшите требуемое GSD до 2.9 см/пиксель" in message
    assert "более крупный" not in message


def test_api_rejection_contains_recommendation(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    resp = client.post("/api/plans", data={"task_id": _create_task(client, env_id, gsd_cm="3.0")})
    assert resp.status_code == 422
    assert "2.9 см/пиксель" in json.dumps(resp.json(), ensure_ascii=False)


# ---------- рекомендация в отчете проверки ----------

START = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)


def _altitude_checks(route_z):
    """План вручную: номинальная H = 140 м, но облет рельефа местами поднял
    маршрут выше (рельеф в тестах плоский, 0 м, поэтому AGL = Z)."""
    coords = [[37.575 + 0.003 * i, 55.704, z] for i, z in enumerate(route_z)]
    route = {"type": "LineString", "coordinates": coords}
    airspace = {"type": "Polygon", "coordinates": square_coords(37.55, 55.70, 0.10, 0.01)}
    env = SimpleNamespace(layers={
        "airspace": [{"type": "Feature", "properties": {"layer": "airspace", "h_min": 0, "h_max": 300}, "geometry": airspace}],
        "no_fly": [], "obstacle": [], "reserve_site": [],
        "launch_site": [{"type": "Feature", "properties": {"layer": "launch_site"},
                         "geometry": {"type": "Point", "coordinates": [37.575, 55.704]}}],
    })
    task = SimpleNamespace(area={"type": "Polygon", "coordinates": square_coords(37.58, 55.702, 0.005, 0.004)},
                           window_start=None, window_end=None, timezone=None, gsd_cm=2.7)
    sortie = SimpleNamespace(uav_id="A", sortie_index=0, track_geojson=route,
                             survey_tracks_geojson={"type": "MultiLineString", "coordinates": [coords[:2]]},
                             start_utc=START, end_utc=START + timedelta(minutes=10))
    plan = SimpleNamespace(height_m=140.0, cruise_speed_mps=10.0, budget_s=3600.0, swath_m=100.0,
                           camera_key="pf1b", sorties=[sortie])
    return {c.name: c for c in safety_service._run_checks(env, task, plan)}


def test_altitude_violation_recommends_max_gsd_with_terrain_margin():
    # Плато 165 м шириной в несколько вершин (~190 м между вершинами) —
    # чтобы на него попали отсчеты проверки высоты (каждые 300 м).
    checks = _altitude_checks([140, 165, 165, 165, 165, 140])
    altitude = checks["altitude"]
    assert not altitude.passed
    # Превышение над номинальной высотой 25 м -> номинальная H не выше 125 м.
    expected = max_gsd_for_height(CAMERA_SPECS["pf1b"], 125.0)
    assert len(altitude.recommendations) == 1
    rec = altitude.recommendations[0]
    assert f"не более {expected:g} см/пиксель" in rec
    assert "GSD 2.7" in rec and "150 м" in rec and "25 м" in rec
    assert survey_height(CAMERA_SPECS["pf1b"], expected) + 25 <= 150


def test_passed_checks_have_no_recommendations():
    checks = _altitude_checks([140, 140, 140])
    assert checks["altitude"].passed and checks["altitude"].recommendations == []


# ---------- запретные зоны: буфер и перестроение маршрута ----------

NFZ = square_coords(37.582, 55.7035, 0.0005, 0.0005)


def _nfz_feature(coords=NFZ):
    return {"type": "Feature", "properties": {"layer": "no_fly", "safety_buffer_m": 0},
            "geometry": {"type": "Polygon", "coordinates": coords}}


def test_extra_buffer_moves_route_away_from_no_fly_zone(client):
    env_id = _upload_environment(client, extra_features=[_nfz_feature()])
    _upload_fleet(client)
    task_id = _create_task(client, env_id)

    zone = shape({"type": "Polygon", "coordinates": NFZ})
    projector = Projector.for_geometry(zone)
    zone_utm = projector.to_utm(zone)

    def clearance(plan_id):
        plan = client.get(f"/api/plans/{plan_id}").json()
        return plan, min(zone_utm.distance(projector.to_utm(shape(s["track_geojson"]))) for s in plan["sorties"])

    base, base_clearance = clearance(plan_service.create_plan(task_id).id)
    buffered, buffered_clearance = clearance(plan_service.create_plan(task_id, extra_no_fly_buffer_m=30).id)

    assert base_clearance < 30  # без запаса маршрут проходит у самой зоны
    assert buffered_clearance >= 30 - 0.5  # с запасом весь маршрут (галсы и переходы) держит 30 м
    assert any("увеличен на 30 м" in w for w in buffered["warnings"])
    assert any("увеличен на 30 м" in line for line in buffered["calculation_log"])
    assert not any("увеличен" in w for w in base["warnings"])


def test_geozone_violation_recalc_escalates_buffer_and_explains(client, monkeypatch):
    # «Стена» БПЗ поперек пути от площадки к области — обхода нет, нарушение
    # неустранимо; каждая попытка автопересчета наращивает буфер на шаг.
    wall = _nfz_feature(square_coords(37.565, 55.0, 0.01, 1.4))
    env_id = _upload_environment(client, extra_features=[wall])
    _upload_fleet(client)
    plan = _plan(client, _create_task(client, env_id))

    requested_buffers = []
    real_create_plan = plan_service.create_plan

    def spy(task_id, progress=None, *, extra_no_fly_buffer_m=0.0):
        requested_buffers.append(extra_no_fly_buffer_m)
        return real_create_plan(task_id, progress, extra_no_fly_buffer_m=extra_no_fly_buffer_m)

    monkeypatch.setattr(plan_service, "create_plan", spy)
    report = client.post("/api/safety-checks", data={"plan_id": plan["id"]}).json()

    assert report["auto_recalc_count"] == 3
    assert requested_buffers == [20.0, 40.0, 60.0]
    geozones = next(c for c in report["checks"] if c["name"] == "geozones")
    assert not geozones["passed"]
    assert "дополнительным буфером 60 м" in geozones["recommendations"][0]

    latest = client.get(f"/api/plans/{report['plan_id']}").json()
    assert any("увеличен на 60 м" in w for w in latest["warnings"])

    # Рекомендации сохраняются вместе с отчетом, а не только в ответе.
    stored = client.get("/api/safety-checks/latest", params={"plan_id": report["plan_id"]}).json()
    stored_geozones = next(c for c in stored["checks"] if c["name"] == "geozones")
    assert stored_geozones["recommendations"] == geozones["recommendations"]
