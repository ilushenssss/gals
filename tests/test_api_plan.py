"""Тесты API модуля «Планирование» — ПЛН.ФТ.2-3, ПЛН.ФТ.5-10."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from uav_planner.api.app import app
from uav_planner.api import fleet_service, plan_service, service as environment_service, task_service


@pytest.fixture(autouse=True)
def _clear_stores():
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleet = None
    plan_service._plans.clear()
    plan_service._plan_ids_by_task.clear()
    yield
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleet = None
    plan_service._plans.clear()
    plan_service._plan_ids_by_task.clear()


@pytest.fixture
def client():
    return TestClient(app)


def square_coords(x0, y0, w, h):
    return [[[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h], [x0, y0]]]


def _upload_environment(client, with_launch_site=True):
    features = [
        {
            "type": "Feature",
            "properties": {"layer": "airspace", "h_min": 0, "h_max": 300},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.55, 55.70, 0.10, 0.01)},
        },
    ]
    if with_launch_site:
        features.append({
            "type": "Feature",
            "properties": {"layer": "launch_site", "name": "ВПП-1"},
            "geometry": {"type": "Point", "coordinates": [37.56, 55.705]},
        })
    scene = {"type": "FeatureCollection", "features": features}
    data = json.dumps(scene).encode("utf-8")
    resp = client.post(
        "/api/environments",
        data={"name": "Обстановка"},
        files={"file": ("scene.geojson", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _upload_fleet(client, n=1, model="geoscan-gemini", status="Готов"):
    records = [
        {"inventory_number": f"{model}-{i}", "model": model, "status": status}
        for i in range(n)
    ]
    data = json.dumps(records).encode("utf-8")
    resp = client.post("/api/fleet", files={"file": ("fleet.json", io.BytesIO(data), "application/json")})
    assert resp.status_code == 200


def _create_task(client, env_id, **overrides):
    form = {
        "name": "Задача 1",
        "environment_id": env_id,
        "survey_type": "RGB",
        "gsd_cm": "3.0",
        "work_date": "2026-06-15",
        "criterion_mode": "Время",
    }
    form.update(overrides)
    area = {"type": "Polygon", "coordinates": square_coords(37.58, 55.702, 0.005, 0.004)}
    resp = client.post(
        "/api/tasks",
        data=form,
        files={"area_file": ("area.geojson", io.BytesIO(json.dumps(area).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_create_plan_happy_path(client):
    env_id = _upload_environment(client)
    _upload_fleet(client, n=2)
    task_id = _create_task(client, env_id)

    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_id"] == task_id
    assert body["version"] == 1
    assert body["sortie_count"] >= 1
    assert body["j1_s"] > 0
    assert body["j2_s"] > 0
    assert body["uav_model"] == "Геоскан Gemini"
    assert body["is_optimal"] is False


def test_create_plan_marks_task_calculated(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    task_id = _create_task(client, env_id)

    client.post("/api/plans", data={"task_id": task_id})
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "Рассчитана"


def test_get_plan_returns_sorties_with_route_geometry(client):
    env_id = _upload_environment(client)
    _upload_fleet(client, n=2)
    task_id = _create_task(client, env_id)
    summary = client.post("/api/plans", data={"task_id": task_id}).json()

    detail = client.get(f"/api/plans/{summary['id']}").json()
    assert len(detail["sorties"]) == summary["sortie_count"]
    for sortie in detail["sorties"]:
        assert sortie["track_geojson"]["type"] == "LineString"
        assert sortie["flight_time_s"] > 0
        assert sortie["end_utc"] > sortie["start_utc"]


def test_second_calculation_creates_new_version(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    task_id = _create_task(client, env_id)

    first = client.post("/api/plans", data={"task_id": task_id}).json()
    second = client.post("/api/plans", data={"task_id": task_id}).json()
    assert second["version"] == first["version"] + 1

    versions = client.get("/api/plans", params={"task_id": task_id}).json()
    assert [v["version"] for v in versions] == [2, 1]


def test_plan_without_fleet_is_infeasible(client):
    env_id = _upload_environment(client)
    task_id = _create_task(client, env_id)
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422
    assert "парк" in resp.json()["detail"]


def test_plan_with_incompatible_survey_type_is_infeasible(client):
    env_id = _upload_environment(client)
    _upload_fleet(client, model="geoscan-801")  # тепловизор/RGB 12Мп, не мультиспектральный
    task_id = _create_task(client, env_id, survey_type="мультиспектральная")
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422
    assert "нагрузк" in resp.json()["detail"]


def test_plan_with_lidar_survey_type_is_infeasible(client):
    env_id = _upload_environment(client)
    _upload_fleet(client)
    task_id = _create_task(client, env_id, survey_type="LiDAR")
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422
    assert "LiDAR" in resp.json()["detail"] or "не поддерживается" in resp.json()["detail"]


def test_plan_without_launch_site_uses_area_centroid(client):
    env_id = _upload_environment(client, with_launch_site=False)
    _upload_fleet(client)
    task_id = _create_task(client, env_id)
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200
    detail = client.get(f"/api/plans/{resp.json()['id']}").json()
    assert detail["sorties"][0]["takeoff_site"] is None


def test_create_plan_for_unknown_task_returns_404(client):
    resp = client.post("/api/plans", data={"task_id": "does-not-exist"})
    assert resp.status_code == 404


def test_get_unknown_plan_returns_404(client):
    resp = client.get("/api/plans/does-not-exist")
    assert resp.status_code == 404


def test_higher_wind_speed_increases_total_flight_time(client):
    # Крейсерская скорость = максимум модели минус скорость ветра (см. известные
    # ограничения) -> при том же покрытии больший ветер должен увеличивать J2.
    env_id = _upload_environment(client)
    _upload_fleet(client, n=2)

    task_calm = _create_task(client, env_id, name="Без ветра", wind_speed_ms="0")
    task_windy = _create_task(client, env_id, name="С ветром", wind_speed_ms="5")

    plan_calm = client.post("/api/plans", data={"task_id": task_calm}).json()
    plan_windy = client.post("/api/plans", data={"task_id": task_windy}).json()

    assert plan_windy["j2_s"] > plan_calm["j2_s"]
