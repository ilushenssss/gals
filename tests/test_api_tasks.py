"""Тесты API модуля «Задача» — ЗАД.ФТ.2-5, ЗАД.ФТ.9-12."""

import io
import json

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


def _area_file(coords):
    geom = {"type": "Polygon", "coordinates": coords}
    return {"area_file": ("area.geojson", io.BytesIO(json.dumps(geom).encode("utf-8")), "application/json")}


def _base_form(environment_id, **overrides):
    form = {
        "name": "Задача 1",
        "environment_id": environment_id,
        "survey_type": "RGB",
        "gsd_cm": "3.0",
        "work_date": "2026-06-15",
        "criterion_mode": "Время",
    }
    form.update(overrides)
    return form


def test_create_task_happy_path(client):
    env_id = _upload_environment(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Черновик"
    assert body["version"] == 1
    assert body["environment_name"] == "Обстановка"


def test_create_task_rejects_area_outside_allowed_space(client):
    env_id = _upload_environment(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id),
        files=_area_file(square_coords(50.0, 50.0, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "area" for i in resp.json()["detail"])


def test_create_task_rejects_non_positive_gsd(client):
    env_id = _upload_environment(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, gsd_cm="0"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "gsd_cm" for i in resp.json()["detail"])


def test_create_task_requires_alpha_for_compromise(client):
    env_id = _upload_environment(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, criterion_mode="Компромисс"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "criterion_alpha" for i in resp.json()["detail"])


def test_create_task_accepts_compromise_with_alpha(client):
    env_id = _upload_environment(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, criterion_mode="Компромисс", criterion_alpha="0.4"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 200


def test_create_task_rejects_window_start_after_end(client):
    env_id = _upload_environment(client)
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id, window_start="18:00", window_end="08:00"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "window" for i in resp.json()["detail"])


def test_create_task_rejects_self_intersecting_area(client):
    env_id = _upload_environment(client)
    bowtie = [[[37.2, 55.2], [37.3, 55.3], [37.3, 55.2], [37.2, 55.3], [37.2, 55.2]]]
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id),
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
    resp = client.post(
        "/api/tasks",
        data=_base_form(env_id),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
    assert any(i["field"] == "area" for i in resp.json()["detail"])


def test_daylight_warning_present_for_polar_night(client):
    env_id = _upload_environment(client)
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
        data=_base_form(env_id, work_date="2026-12-21"),
        files=_area_file(square_coords(15.05, 78.05, 0.05)),
    )
    assert resp.status_code == 200
    assert resp.json()["daylight_warning"] is not None


def test_list_and_get_task(client):
    env_id = _upload_environment(client)
    created = client.post(
        "/api/tasks", data=_base_form(env_id), files=_area_file(square_coords(37.2, 55.2, 0.2))
    ).json()

    listed = client.get("/api/tasks", params={"environment_id": env_id}).json()
    assert any(t["id"] == created["id"] for t in listed)

    detail = client.get(f"/api/tasks/{created['id']}").json()
    assert detail["area"]["type"] == "Polygon"


def test_update_task_with_correct_version_succeeds(client):
    env_id = _upload_environment(client)
    created = client.post(
        "/api/tasks", data=_base_form(env_id), files=_area_file(square_coords(37.2, 55.2, 0.2))
    ).json()

    form = _base_form(env_id, name="Задача обновлена")
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
    created = client.post(
        "/api/tasks", data=_base_form(env_id), files=_area_file(square_coords(37.2, 55.2, 0.2))
    ).json()

    form = _base_form(env_id)
    form["expected_version"] = "999"
    resp = client.put(
        f"/api/tasks/{created['id']}", data=form, files=_area_file(square_coords(37.2, 55.2, 0.2))
    )
    assert resp.status_code == 409


def test_update_unknown_task_returns_404(client):
    env_id = _upload_environment(client)
    form = _base_form(env_id)
    form["expected_version"] = "1"
    resp = client.put("/api/tasks/does-not-exist", data=form, files=_area_file(square_coords(37.2, 55.2, 0.2)))
    assert resp.status_code == 404


def test_create_task_with_unknown_environment_returns_400(client):
    resp = client.post(
        "/api/tasks",
        data=_base_form("does-not-exist"),
        files=_area_file(square_coords(37.2, 55.2, 0.2)),
    )
    assert resp.status_code == 400
