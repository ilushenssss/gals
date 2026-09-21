"""Тесты API модуля «Задача» — ЗАД.ФТ.2-5, ЗАД.ФТ.9-12."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from uav_planner.api.app import app
from uav_planner.api import fleet_service
from uav_planner.api import service as environment_service
from uav_planner.api import task_service


@pytest.fixture(autouse=True)
def _clear_stores():
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleets.clear()
    yield
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleets.clear()


@pytest.fixture
def client():
    return TestClient(app)


def square_coords(x0, y0, size):
    return [[[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]]


def _upload_environment(client, h_max=500):
    scene = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "airspace", "h_min": 0, "h_max": h_max},
                "geometry": {"type": "Polygon", "coordinates": square_coords(37.0, 55.0, 1.0)},
            },
        ],
    }
    data = json.dumps(scene).encode("utf-8")
    resp = client.post(
        "/api/environments",
        data={"name": "Обстановка"},
        files={"file": ("scene.geojson", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _upload_fleet(client, location_lat=55.5, location_lon=37.5):
    # Локация парка теперь определяется из файла (по запросу пользователя),
    # не вводится вручную — задаём её через location_lat/lon экземпляра.
    # По умолчанию — внутри области облета тестовых обстановок этого файла
    # (square_coords(37.0, 55.0, 1.0)), чтобы проверка совместимости парка и
    # обстановки по расстоянию (task_service) не мешала тестам, которые ее не
    # проверяют напрямую.
    records = [{
        "inventory_number": "GEM-1", "model": "geoscan-gemini", "status": "Готов",
        "location_lat": location_lat, "location_lon": location_lon,
    }]
    resp = client.post(
        "/api/fleets",
        data={"name": "Парк"},
        files={"file": ("fleet.json", io.BytesIO(json.dumps(records).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _area_file(coords):
    geom = {"type": "Polygon", "coordinates": coords}
    return {"area_file": ("area.geojson", io.BytesIO(json.dumps(geom).encode("utf-8")), "application/json")}


def _base_form(environment_id, fleet_id, **overrides):
    form = {
        "name": "Задача 1",
        "environment_id": environment_id,
        "fleet_id": fleet_id,
        "survey_type": "RGB",
        "gsd_cm": "3.0",
        "work_date": "2026-06-15",
        "criterion_mode": "Время",
    }
    form.update(overrides)
    return form


def test_create_task_happy_path(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Черновик"
    assert body["version"] == 1
    assert body["environment_name"] == "Обстановка"


def test_create_task_rejects_area_outside_allowed_space(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id),
        files=_area_file(square_coords(50.0, 50.0, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "area" for i in resp.json()["detail"])


def test_create_task_rejects_non_positive_gsd(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id, gsd_cm="0"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "gsd_cm" for i in resp.json()["detail"])


def test_create_task_requires_alpha_for_compromise(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id, criterion_mode="Компромисс"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "criterion_alpha" for i in resp.json()["detail"])


def test_create_task_accepts_compromise_with_alpha(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id, criterion_mode="Компромисс", criterion_alpha="0.4"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 200


def test_create_task_rejects_window_start_after_end(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id, window_start="18:00", window_end="08:00"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "window" for i in resp.json()["detail"])


def test_create_task_rejects_self_intersecting_area(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    bowtie = [[[37.2, 55.2], [37.3, 55.3], [37.3, 55.2], [37.2, 55.3], [37.2, 55.2]]]
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id),
        files=_area_file(bowtie),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "area" for i in resp.json()["detail"])


def test_create_task_rejects_environment_without_airspace(client):
    # Обстановка без airspace -> при загрузке будет "Корректна" (нет ошибок валидации),
    # но без зон область не сможет пересечься ни с чем -> ошибка на area.
    scene = {"type": "FeatureCollection", "features": []}
    data = json.dumps(scene).encode("utf-8")
    resp = client.post(
        "/api/environments",
        data={"name": "Пустая"},
        files={"file": ("scene.geojson", io.BytesIO(data), "application/json")},
    )
    env_id = resp.json()["id"]
    fleet_id = _upload_fleet(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "area" for i in resp.json()["detail"])


def test_daylight_warning_present_for_polar_night(client):
    env_id = _upload_environment(client)
    # Локация парка по умолчанию (Москва) слишком далеко от Арктики — эта
    # проверка не о совместимости парка/обстановки, поэтому парк ставим рядом.
    fleet_id = _upload_fleet(client, location_lat=78.0, location_lon=15.0)
    resp = client.post(
        "/api/environments",
        data={"name": "Арктика"},
        files={"file": (
            "scene.geojson",
            io.BytesIO(json.dumps({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"layer": "airspace", "h_min": 0, "h_max": 500},
                    "geometry": {"type": "Polygon", "coordinates": square_coords(15.0, 78.0, 0.2)},
                }],
            }).encode("utf-8")),
            "application/json",
        )},
    )
    env_id = resp.json()["id"]
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id, work_date="2026-12-21"),
        files=_area_file(square_coords(15.05, 78.05, 0.05)),
    )
    assert resp.status_code == 200
    assert resp.json()["daylight_warning"] is not None


def test_list_and_get_task(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    created = client.post(
        "/api/tasks", data=_base_form(env_id, fleet_id), files=_area_file(square_coords(37.2, 55.2, 0.2))
    ).json()

    listed = client.get("/api/tasks", params={"environment_id": env_id}).json()
    assert any(t["id"] == created["id"] for t in listed)
    # Область облета нужна уже в сводке — для миниатюры карты на карточке
    # списка задач, без отдельного запроса на каждую задачу.
    listed_task = next(t for t in listed if t["id"] == created["id"])
    assert listed_task["area"]["type"] == "Polygon"

    detail = client.get(f"/api/tasks/{created['id']}").json()
    assert detail["area"]["type"] == "Polygon"


def test_update_task_with_correct_version_succeeds(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    created = client.post(
        "/api/tasks", data=_base_form(env_id, fleet_id), files=_area_file(square_coords(37.2, 55.2, 0.2))
    ).json()

    form = _base_form(env_id, fleet_id, name="Задача обновлена")
    form["expected_version"] = "1"
    resp = client.put(
        f"/api/tasks/{created['id']}", data=form, files=_area_file(square_coords(37.2, 55.2, 0.2))
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Задача обновлена"
    assert body["version"] == 2


def test_update_task_with_stale_version_conflicts(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    created = client.post(
        "/api/tasks", data=_base_form(env_id, fleet_id), files=_area_file(square_coords(37.2, 55.2, 0.2))
    ).json()

    form = _base_form(env_id, fleet_id)
    form["expected_version"] = "999"
    resp = client.put(
        f"/api/tasks/{created['id']}", data=form, files=_area_file(square_coords(37.2, 55.2, 0.2))
    )
    assert resp.status_code == 409


def test_update_unknown_task_returns_404(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    form = _base_form(env_id, fleet_id)
    form["expected_version"] = "1"
    resp = client.put("/api/tasks/does-not-exist", data=form, files=_area_file(square_coords(37.2, 55.2, 0.2)))
    assert resp.status_code == 404


def test_create_task_with_unknown_environment_returns_400(client):
    resp = client.post(
        "/api/tasks",
        data=_base_form("does-not-exist", "does-not-exist"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400


def test_create_task_with_unknown_fleet_returns_400(client):
    env_id = _upload_environment(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, "does-not-exist"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "fleet_id" for i in resp.json()["detail"])


def test_create_task_rejects_incompatible_fleet_and_environment(client):
    # По запросу пользователя: парк из одного региона (Москва) и обстановка
    # из другого (Екатеринбург, ~1400 км) — несовместимы, задачу не создать.
    env_id = _upload_environment(client)  # square_coords(37.0, 55.0, 1.0) — Москва
    fleet_id = _upload_fleet(client, location_lat=56.8389, location_lon=60.6057)  # Екатеринбург
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400, resp.text
    issues = resp.json()["detail"]
    assert any(i["field"] == "fleet_id" and "далеко" in i["message"] for i in issues)


def test_create_task_accepts_fleet_within_the_same_region(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client, location_lat=55.4, location_lon=37.4)  # рядом с обстановкой
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, fleet_id),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fleet_id"] == fleet_id
