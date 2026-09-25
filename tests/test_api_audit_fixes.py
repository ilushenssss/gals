"""Регрессии дефектов, найденных аудитом (docs/AUDIT.md, раздел «Дефекты»):
часовой пояс окна работ, разведение бортов по времени, ветровое ограничение
модели, буфер препятствия, интервалы действия зон, перерезка вылета после
обхода зон."""

import io
import json
from datetime import datetime

import pytest
from shapely.geometry import LineString, Point, shape

from test_api_plan import _create_task, _upload_fleet, square_coords

from uav_planner.config import get_settings
from uav_planner.geometry import Projector
from uav_planner.services import plan_service

AREA = {"type": "Polygon", "coordinates": square_coords(37.58, 55.702, 0.005, 0.004)}


def _upload_environment(client, extra_features=(), airspace_props=None):
    features = [
        {
            "type": "Feature",
            "properties": {"layer": "airspace", "h_min": 0, "h_max": 300, **(airspace_props or {})},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.55, 55.70, 0.10, 0.01)},
        },
        {
            "type": "Feature",
            "properties": {"layer": "launch_site", "name": "ВПП-1"},
            "geometry": {"type": "Point", "coordinates": [37.56, 55.705]},
        },
        *extra_features,
    ]
    data = json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")
    resp = client.post(
        "/api/environments", data={"name": "Обстановка"},
        files={"file": ("scene.geojson", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Корректна", resp.text
    return resp.json()["id"]


def _plan(client, task_id):
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200, resp.text
    return client.get(f"/api/plans/{resp.json()['id']}").json()


# ---------- окно работ в местном времени ----------

def test_work_window_is_interpreted_in_task_timezone(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    task_id = _create_task(
        client, env_id, window_start="09:00", window_end="12:00", timezone="Europe/Moscow",
    )
    assert client.get(f"/api/tasks/{task_id}").json()["timezone"] == "Europe/Moscow"

    plan = _plan(client, task_id)
    starts = [datetime.fromisoformat(s["start_utc"]) for s in plan["sorties"]]
    ends = [datetime.fromisoformat(s["end_utc"]) for s in plan["sorties"]]
    # 09:00–12:00 МСК = 06:00–09:00 UTC (раньше — 09:00–12:00 UTC).
    assert min(starts).hour == 6 and min(starts).minute == 0
    assert max(ends).hour < 9
    assert any("Europe/Moscow" in line for line in plan["calculation_log"])


def test_task_without_timezone_keeps_utc_semantics(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    task_id = _create_task(client, env_id, window_start="09:00", window_end="12:00")
    assert client.get(f"/api/tasks/{task_id}").json()["timezone"] is None
    plan = _plan(client, task_id)
    assert min(datetime.fromisoformat(s["start_utc"]) for s in plan["sorties"]).hour == 9


def test_unknown_timezone_is_rejected(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    form = {
        "name": "Задача", "environment_id": env_id, "fleet_id": client.get("/api/fleets").json()[0]["id"],
        "survey_type": "RGB", "gsd_cm": "1.9", "work_date": "2026-06-15", "criterion_mode": "Время",
        "timezone": "Mars/Olympus",
    }
    resp = client.post(
        "/api/tasks", data=form,
        files={"area_file": ("area.geojson", io.BytesIO(json.dumps(AREA).encode()), "application/json")},
    )
    assert resp.status_code == 400
    assert any(issue["field"] == "timezone" for issue in resp.json()["detail"])


# ---------- разведение бортов одной площадки ----------

def test_vehicles_from_one_site_do_not_take_off_simultaneously(client):
    # Регрессия: два борта одной площадки взлетали в одну секунду, и
    # проверка «Разведение» находила сближение до 0 м.
    env_id = _upload_environment(client)
    _upload_fleet(client, n=2)
    plan = _plan(client, _create_task(client, env_id))
    first_starts = {}
    for s in plan["sorties"]:
        first_starts.setdefault(s["uav_id"], datetime.fromisoformat(s["start_utc"]))
    assert len(first_starts) == 2
    a, b = sorted(first_starts.values())
    assert (b - a).total_seconds() >= 60

    report = client.post("/api/safety-checks", data={"plan_id": plan["id"]}).json()
    separation = next(c for c in report["checks"] if c["name"] == "separation")
    assert separation["passed"], separation["violations"]


# ---------- ветровое ограничение модели ----------

def test_wind_above_model_limit_makes_plan_infeasible(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    task_id = _create_task(client, env_id, wind_speed_ms="15")  # Gemini: не более 12 м/с
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422, resp.text
    assert "ветер задачи 15 м/с превышает допустимый" in json.dumps(resp.json(), ensure_ascii=False)


# ---------- буфер безопасности препятствия ----------

def test_survey_tracks_keep_obstacle_safety_buffer(client):
    obstacle_coords = square_coords(37.582, 55.7035, 0.0005, 0.0005)
    env_id = _upload_environment(client, extra_features=[{
        "type": "Feature",
        "properties": {"layer": "obstacle", "h_min": 0, "h_max": 500, "safety_buffer_m": 40},
        "geometry": {"type": "Polygon", "coordinates": obstacle_coords},
    }])
    _upload_fleet(client)
    plan = _plan(client, _create_task(client, env_id))

    obstacle = shape({"type": "Polygon", "coordinates": obstacle_coords})
    projector = Projector.for_geometry(obstacle)
    obstacle_utm = projector.to_utm(obstacle)
    survey = [projector.to_utm(shape(s["survey_tracks_geojson"])) for s in plan["sorties"]]
    nearest = min(obstacle_utm.distance(t) for t in survey)
    # Раньше буфер препятствия терялся, и галсы подходили к нему вплотную.
    assert nearest >= 40 - 0.5


# ---------- интервалы действия зон ----------

def test_allowed_zone_inactive_during_work_window_makes_plan_infeasible(client):
    env_id = _upload_environment(client, airspace_props={
        "active_windows": [{"start": "2026-06-15T10:00:00+00:00", "end": "2026-06-15T11:00:00+00:00"}],
    })
    _upload_fleet(client)
    task_id = _create_task(client, env_id, window_start="06:00", window_end="12:00")
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422, resp.text
    assert "не действует в течение всего окна работ" in json.dumps(resp.json(), ensure_ascii=False)


def test_allowed_zone_active_through_work_window_is_used(client):
    env_id = _upload_environment(client, airspace_props={
        "active_windows": [{"start": "2026-06-15T05:00:00", "end": "2026-06-15T13:00:00"}],  # без пояса = UTC
    })
    _upload_fleet(client)
    plan = _plan(client, _create_task(client, env_id, window_start="06:00", window_end="12:00"))
    report = client.post("/api/safety-checks", data={"plan_id": plan["id"]}).json()
    airspace = next(c for c in report["checks"] if c["name"] == "airspace")
    assert airspace["passed"], airspace["violations"]


# ---------- вылет сверх бюджета после обхода зон ----------

@pytest.fixture
def tight_budget(monkeypatch):
    """Бюджет вылета ≈8.4 мин (резерв 79 % от 40 мин): миссия сцены — около
    8.7 мин по прямой, поэтому маршрутизация режет ее на вылеты, первый из
    которых упакован почти впритык к бюджету."""
    monkeypatch.setenv("GALS_ENERGY_RESERVE", "0.79")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def test_sortie_pushed_over_budget_by_detours_is_resplit(client, tight_budget, monkeypatch):
    # Обход зон, удлиняющий каждый переход на 30 %: маршрутизация считала по
    # прямой, и вылеты, упакованные впритык, выходят за бюджет.
    real_find_path = plan_service.find_path

    def detouring_find_path(start, goal, *args, **kwargs):
        result = real_find_path(start, goal, *args, **kwargs)
        line = result.line
        if line.length < 10:
            return result
        mid = line.interpolate(0.5, normalized=True)
        dx, dy = goal.x - start.x, goal.y - start.y
        norm = (dx * dx + dy * dy) ** 0.5
        offset = line.length * 0.415  # 2·√(0.5² + 0.415²) ≈ 1.3
        apex = Point(mid.x - dy / norm * offset, mid.y + dx / norm * offset)
        return type(result)(LineString([start, apex, goal]), True, False)

    monkeypatch.setattr(plan_service, "find_path", detouring_find_path)
    env_id = _upload_environment(client)
    _upload_fleet(client)
    plan = _plan(client, _create_task(client, env_id))

    assert any("перераспределены на дополнительные вылеты" in line for line in plan["calculation_log"])
    for s in plan["sorties"]:
        survey_count = len(shape(s["survey_tracks_geojson"]).geoms)
        assert s["flight_time_s"] <= plan["budget_s"] + 1e-6 or survey_count == 1
