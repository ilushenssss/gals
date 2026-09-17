"""Тесты API модуля «Парк БВС» — ПБС.ФТ.2-4, ПБС.ФТ.10-11."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from uav_planner.api.app import app
from uav_planner.api import fleet_service


@pytest.fixture(autouse=True)
def _clear_fleet():
    fleet_service._fleet = None
    yield
    fleet_service._fleet = None


@pytest.fixture
def client():
    return TestClient(app)


def _upload_json(client, records):
    data = json.dumps(records).encode("utf-8")
    return client.post("/api/fleet", files={"file": ("fleet.json", io.BytesIO(data), "application/json")})


def test_upload_valid_fleet(client):
    resp = _upload_json(client, [
        {"inventory_number": "201-01", "model": "geoscan-201", "status": "Готов", "base_launch_site": "ВПП-1"},
        {"inventory_number": "GEM-01", "model": "geoscan-gemini", "status": "На обслуживании"},
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Корректна"
    assert body["total"] == 2
    assert body["ready_count"] == 1
    assert body["errors"] == []


def test_get_fleet_returns_instances(client):
    _upload_json(client, [{"inventory_number": "201-01", "model": "geoscan-201"}])
    resp = client.get("/api/fleet")
    assert resp.status_code == 200
    body = resp.json()
    assert body["instances"][0]["model_name"] == "Геоскан 201"
    assert body["instances"][0]["status"] == "Готов"  # значение по умолчанию


def test_get_fleet_without_upload_returns_404(client):
    resp = client.get("/api/fleet")
    assert resp.status_code == 404


def test_duplicate_inventory_number_is_flagged(client):
    resp = _upload_json(client, [
        {"inventory_number": "201-01", "model": "geoscan-201"},
        {"inventory_number": "201-01", "model": "geoscan-gemini"},
    ])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("дублирующийся" in e["message"] for e in body["errors"])

    detail = client.get("/api/fleet").json()
    assert detail["instances"][0]["valid"] is True
    assert detail["instances"][1]["valid"] is False


def test_unknown_model_is_flagged(client):
    resp = _upload_json(client, [{"inventory_number": "X-1", "model": "not-a-model"}])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("неизвестная модель" in e["message"] for e in body["errors"])


def test_invalid_status_is_flagged(client):
    resp = _upload_json(client, [{"inventory_number": "X-1", "model": "geoscan-801", "status": "Летит"}])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("недопустимый статус" in e["message"] for e in body["errors"])


def test_missing_inventory_number_is_flagged(client):
    resp = _upload_json(client, [{"model": "geoscan-801"}])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("инвентарный номер" in e["message"] for e in body["errors"])


def test_csv_upload_is_supported(client):
    csv_text = "inventory_number,model,status,base_launch_site\n201-01,geoscan-201,Готов,ВПП-1\n"
    resp = client.post("/api/fleet", files={"file": ("fleet.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Корректна"
    assert body["total"] == 1


def test_second_upload_replaces_first(client):
    _upload_json(client, [{"inventory_number": "A", "model": "geoscan-201"}])
    _upload_json(client, [{"inventory_number": "B", "model": "geoscan-gemini"}])
    detail = client.get("/api/fleet").json()
    assert detail["total"] == 1
    assert detail["instances"][0]["inventory_number"] == "B"


def test_fleet_models_reference_matches_concept_table(client):
    resp = client.get("/api/fleet/models")
    assert resp.status_code == 200
    models = {m["key"]: m for m in resp.json()}
    assert models["geoscan-201"]["uav_type"] == "самолет"
    assert models["geoscan-201"]["max_wind_ms"] == 12.0
    assert models["geoscan-gemini"]["height_max_m"] == 500.0
    assert "pf1b" in models["geoscan-gemini"]["compatible_cameras"]


def test_eligible_instances_excludes_unavailable(client):
    _upload_json(client, [
        {"inventory_number": "A", "model": "geoscan-201", "status": "Готов"},
        {"inventory_number": "B", "model": "geoscan-201", "status": "Недоступен"},
        {"inventory_number": "C", "model": "geoscan-201", "status": "На обслуживании"},
    ])
    eligible = fleet_service.eligible_instances()
    assert [i.inventory_number for i in eligible] == ["A"]


def test_single_line_without_json_markers_is_treated_as_empty_csv(client):
    # Не начинается с '[' или '{' -> разбирается как CSV; одна строка без запятых
    # становится заголовком единственной колонки, строк данных нет -> пустой парк.
    resp = client.post("/api/fleet", files={"file": ("bad.json", io.BytesIO(b"not json and not csv without header"), "application/json")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Корректна"
    assert body["total"] == 0


def test_malformed_json_returns_400(client):
    resp = client.post("/api/fleet", files={"file": ("bad.json", io.BytesIO(b"[{invalid json"), "application/json")})
    assert resp.status_code == 400
