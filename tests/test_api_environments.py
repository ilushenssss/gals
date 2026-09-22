"""Тесты API модуля «Обстановка» — ОБС.ФТ.2-4, ОБС.ФТ.8-10."""

import io
import json

def _upload(client, geojson: dict, name: str = "Тестовая сцена"):
    data = json.dumps(geojson).encode("utf-8")
    return client.post(
        "/api/environments",
        data={"name": name},
        files={"file": ("scene.geojson", io.BytesIO(data), "application/json")},
    )


def square_coords(x0, y0, size):
    return [[[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]]


def valid_scene():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "airspace", "h_min": 0, "h_max": 500},
                "geometry": {"type": "Polygon", "coordinates": square_coords(37.0, 55.0, 0.05)},
            },
            {
                "type": "Feature",
                "properties": {"layer": "launch_site", "name": "ВПП-1"},
                "geometry": {"type": "Point", "coordinates": [37.005, 55.005]},
            },
            {
                "type": "Feature",
                "properties": {"layer": "reserve_site", "name": "Резерв-1"},
                "geometry": {"type": "Point", "coordinates": [37.04, 55.04]},
            },
        ],
    }


def test_valid_scene_uploads_successfully(client):
    resp = _upload(client, valid_scene())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Корректна"
    assert body["counts"]["airspace"] == 1
    assert body["counts"]["launch_site"] == 1
    assert body["errors"] == []


def test_uploaded_environment_is_retrievable_with_layers(client):
    summary = _upload(client, valid_scene()).json()
    resp = client.get(f"/api/environments/{summary['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["layers"]["airspace"][0]["properties"]["_valid"] is True
    assert detail["layers"]["launch_site"][0]["properties"]["name"] == "ВПП-1"


def test_list_environments_returns_uploaded_scene(client):
    _upload(client, valid_scene(), name="Сцена A")
    resp = client.get("/api/environments")
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()]
    assert "Сцена A" in names


def test_unknown_layer_property_is_rejected(client):
    scene = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "not_a_real_layer"},
                "geometry": {"type": "Point", "coordinates": [37.0, 55.0]},
            }
        ],
    }
    resp = _upload(client, scene)
    assert resp.status_code == 400
    assert "layer" in resp.json()["detail"]


def test_self_intersecting_polygon_reported_as_error(client):
    scene = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "airspace", "h_min": 0, "h_max": 500},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]],
                },
            }
        ],
    }
    resp = _upload(client, scene)
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert body["errors"][0]["layer"] == "airspace"


def test_missing_height_range_is_reported_as_error(client):
    scene = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "airspace"},  # нет h_min/h_max
                "geometry": {"type": "Polygon", "coordinates": square_coords(37.0, 55.0, 0.05)},
            }
        ],
    }
    resp = _upload(client, scene)
    body = resp.json()
    assert body["status"] == "Содержит ошибки"


def test_launch_site_outside_allowed_airspace_is_rejected(client):
    scene = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "airspace", "h_min": 0, "h_max": 500},
                "geometry": {"type": "Polygon", "coordinates": square_coords(37.0, 55.0, 0.05)},
            },
            {
                "type": "Feature",
                "properties": {"layer": "launch_site", "name": "Далеко"},
                "geometry": {"type": "Point", "coordinates": [40.0, 55.0]},
            },
        ],
    }
    resp = _upload(client, scene)
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any(e["layer"] == "launch_site" for e in body["errors"])


def test_launch_site_inside_no_fly_buffer_is_rejected(client):
    scene = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "airspace", "h_min": 0, "h_max": 500},
                "geometry": {"type": "Polygon", "coordinates": square_coords(37.0, 55.0, 0.05)},
            },
            {
                "type": "Feature",
                "properties": {"layer": "no_fly", "safety_buffer_m": 50},
                "geometry": {"type": "Polygon", "coordinates": square_coords(37.005, 55.005, 0.01)},
            },
            {
                "type": "Feature",
                "properties": {"layer": "launch_site", "name": "В зоне"},
                "geometry": {"type": "Point", "coordinates": [37.008, 55.008]},
            },
        ],
    }
    resp = _upload(client, scene)
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any(e["layer"] == "launch_site" for e in body["errors"])


def test_no_fly_buffer_geojson_is_attached_for_display(client):
    summary = _upload(client, {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer": "no_fly", "safety_buffer_m": 30},
                "geometry": {"type": "Polygon", "coordinates": square_coords(37.0, 55.0, 0.01)},
            }
        ],
    }).json()
    detail = client.get(f"/api/environments/{summary['id']}").json()
    props = detail["layers"]["no_fly"][0]["properties"]
    assert "_buffer_geojson" in props


def test_missing_file_field_returns_422(client):
    resp = client.post("/api/environments", data={"name": "x"})
    assert resp.status_code == 422


def test_get_unknown_environment_returns_404(client):
    resp = client.get("/api/environments/does-not-exist")
    assert resp.status_code == 404


def test_frontend_index_is_served(client):
    """По решению пользователя при переносе продуктового стека из
    merge-core-into-wrapper обратно в main React-SPA не перенесена — этот же
    процесс снова отдает vanilla-JS фронтенд первой версии интерфейса
    (см. api/app.py), как было до переноса."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Галс" in resp.text
