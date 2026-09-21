"""Тесты API модуля «Парк БВС» — ПБС.ФТ.2-4, ПБС.ФТ.10-11.

По запросу пользователя парков несколько, каждый — именованная сущность со
своей локацией (не единственный глобальный парк, который заменялся при
повторной загрузке, как было раньше), и локация парка определяется из самого
файла (координаты экземпляров), а не вводится вручную."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from uav_planner.api.app import app
from uav_planner.api import fleet_service


@pytest.fixture(autouse=True)
def _clear_fleet():
    fleet_service._fleets.clear()
    yield
    fleet_service._fleets.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _rec(inv, model="geoscan-201", status="Готов", lat=56.7625, lon=60.5992, **extra):
    """Запись экземпляра с локацией по умолчанию (Екатеринбург) — большинству
    тестов нужна хоть одна локация в файле, иначе локацию парка не вывести."""
    rec = {"inventory_number": inv, "model": model, "status": status}
    if lat is not None:
        rec["location_lat"] = lat
    if lon is not None:
        rec["location_lon"] = lon
    rec.update(extra)
    return rec


def _upload_json(client, records, name="Парк 1", location_name=None):
    data = json.dumps(records).encode("utf-8")
    form = {"name": name}
    if location_name is not None:
        form["location_name"] = location_name
    return client.post(
        "/api/fleets", data=form, files={"file": ("fleet.json", io.BytesIO(data), "application/json")},
    )


def test_upload_valid_fleet(client):
    resp = _upload_json(client, [
        _rec("201-01", status="Готов", base_launch_site="ВПП-1"),
        _rec("GEM-01", model="geoscan-gemini", status="На обслуживании", lat=56.77, lon=60.60),
    ], name="Парк Екатеринбург", location_name="Екатеринбург")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Корректна"
    assert body["name"] == "Парк Екатеринбург"
    assert body["location_name"] == "Екатеринбург"
    # Локация парка — среднее по локациям обоих экземпляров (56.7625, 56.77).
    assert body["location_lat"] == pytest.approx((56.7625 + 56.77) / 2)
    assert body["total"] == 2
    assert body["ready_count"] == 1
    assert body["errors"] == []


def test_get_fleet_returns_instances(client):
    fleet_id = _upload_json(client, [_rec("201-01")]).json()["id"]
    resp = client.get(f"/api/fleets/{fleet_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["instances"][0]["model_name"] == "Геоскан 201"
    assert body["instances"][0]["status"] == "Готов"  # значение по умолчанию


def test_get_unknown_fleet_returns_404(client):
    resp = client.get("/api/fleets/does-not-exist")
    assert resp.status_code == 404


def test_duplicate_inventory_number_is_flagged(client):
    resp = _upload_json(client, [_rec("201-01"), _rec("201-01", model="geoscan-gemini")])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("дублирующийся" in e["message"] for e in body["errors"])

    detail = client.get(f"/api/fleets/{body['id']}").json()
    assert detail["instances"][0]["valid"] is True
    assert detail["instances"][1]["valid"] is False


def test_unknown_model_is_flagged(client):
    resp = _upload_json(client, [_rec("X-1", model="not-a-model")])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("неизвестная модель" in e["message"] for e in body["errors"])


def test_invalid_status_is_flagged(client):
    resp = _upload_json(client, [_rec("X-1", model="geoscan-801", status="Летит")])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("недопустимый статус" in e["message"] for e in body["errors"])


def test_missing_inventory_number_is_flagged(client):
    resp = _upload_json(client, [_rec(None, model="geoscan-801")])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("инвентарный номер" in e["message"] for e in body["errors"])


def test_location_is_stored_when_valid(client):
    resp = _upload_json(client, [_rec("201-01", lat=55.775, lon=37.60)])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Корректна"
    detail = client.get(f"/api/fleets/{body['id']}").json()
    inst = detail["instances"][0]
    assert inst["location_lat"] == pytest.approx(55.775)
    assert inst["location_lon"] == pytest.approx(37.60)


def test_missing_instance_location_is_not_an_error(client):
    # У первого экземпляра локации нет вовсе (легитимно для конкретного
    # экземпляра) — но локацию парка всё равно можно вывести по второму.
    resp = _upload_json(client, [_rec("201-01", lat=None, lon=None), _rec("201-02")])
    body = resp.json()
    assert body["status"] == "Корректна"
    detail = client.get(f"/api/fleets/{body['id']}").json()
    assert detail["instances"][0]["location_lat"] is None
    assert detail["instances"][0]["location_lon"] is None


def test_partial_location_is_flagged(client):
    # Второй экземпляр с корректной локацией — чтобы локация парка была
    # определима, а некорректная локация первого проверялась независимо.
    records = [{"inventory_number": "201-01", "model": "geoscan-201", "location_lat": 55.775}, _rec("201-02")]
    resp = _upload_json(client, records)
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("некорректная локация" in e["message"] for e in body["errors"])


def test_out_of_range_location_is_flagged(client):
    resp = _upload_json(client, [_rec("201-01", lat=200.0, lon=37.60), _rec("201-02")])
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("некорректная локация" in e["message"] for e in body["errors"])


def test_non_numeric_location_is_flagged(client):
    records = [
        {"inventory_number": "201-01", "model": "geoscan-201", "location_lat": "north", "location_lon": 37.60},
        _rec("201-02"),
    ]
    resp = _upload_json(client, records)
    body = resp.json()
    assert body["status"] == "Содержит ошибки"
    assert any("некорректная локация" in e["message"] for e in body["errors"])


def test_no_instance_locations_at_all_returns_400(client):
    # По запросу пользователя локация парка определяется из файла — если ни у
    # одного экземпляра нет координат, определить её нечем, это явная ошибка.
    resp = _upload_json(client, [_rec("201-01", lat=None, lon=None)])
    assert resp.status_code == 400
    assert "локац" in resp.json()["detail"]


def test_csv_upload_is_supported(client):
    csv_text = "inventory_number,model,status,base_launch_site,location_lat,location_lon\n201-01,geoscan-201,Готов,ВПП-1,56.76,60.60\n"
    resp = client.post(
        "/api/fleets", data={"name": "Парк CSV"},
        files={"file": ("fleet.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Корректна"
    assert body["total"] == 1
    assert body["location_lat"] == pytest.approx(56.76)


def test_csv_upload_with_location(client):
    csv_text = (
        "inventory_number,model,location_lat,location_lon\n"
        "201-01,geoscan-201,55.775,37.60\n"
        "201-02,geoscan-201,,\n"  # локация не указана — не ошибка для этого экземпляра
    )
    resp = client.post(
        "/api/fleets", data={"name": "Парк CSV"},
        files={"file": ("fleet.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
    )
    assert resp.status_code == 200
    fleet_id = resp.json()["id"]
    instances = client.get(f"/api/fleets/{fleet_id}").json()["instances"]
    assert instances[0]["location_lat"] == pytest.approx(55.775)
    assert instances[1]["location_lat"] is None


def test_uploading_a_second_fleet_keeps_the_first(client):
    first = _upload_json(client, [_rec("A")], name="Парк А").json()
    second = _upload_json(client, [_rec("B", model="geoscan-gemini")], name="Парк Б").json()
    assert first["id"] != second["id"]

    listed = {f["id"]: f for f in client.get("/api/fleets").json()}
    assert set(listed) == {first["id"], second["id"]}
    assert client.get(f"/api/fleets/{first['id']}").json()["instances"][0]["inventory_number"] == "A"
    assert client.get(f"/api/fleets/{second['id']}").json()["instances"][0]["inventory_number"] == "B"


def test_fleet_models_reference_matches_concept_table(client):
    resp = client.get("/api/fleets/models")
    assert resp.status_code == 200
    models = {m["key"]: m for m in resp.json()}
    assert models["geoscan-201"]["uav_type"] == "самолет"
    assert models["geoscan-201"]["max_wind_ms"] == 12.0
    assert models["geoscan-gemini"]["height_max_m"] == 500.0
    assert "pf1b" in models["geoscan-gemini"]["compatible_cameras"]


def test_eligible_instances_excludes_unavailable(client):
    fleet_id = _upload_json(client, [
        _rec("A", status="Готов"), _rec("B", status="Недоступен"), _rec("C", status="На обслуживании"),
    ]).json()["id"]
    eligible = fleet_service.eligible_instances(fleet_id)
    assert [i.inventory_number for i in eligible] == ["A"]


def test_eligible_instances_for_unknown_fleet_is_empty(client):
    assert fleet_service.eligible_instances("does-not-exist") == []


def test_single_line_without_json_markers_is_treated_as_empty_csv(client):
    # Не начинается с '[' или '{' -> разбирается как CSV; одна строка без запятых
    # становится заголовком единственной колонки, строк данных нет -> пустой
    # парк без единого экземпляра -> локацию определить нечем, честная 400.
    resp = client.post(
        "/api/fleets", data={"name": "Парк"},
        files={"file": ("bad.json", io.BytesIO(b"not json and not csv without header"), "application/json")},
    )
    assert resp.status_code == 400
    assert "локац" in resp.json()["detail"]


def test_malformed_json_returns_400(client):
    resp = client.post(
        "/api/fleets", data={"name": "Парк"},
        files={"file": ("bad.json", io.BytesIO(b"[{invalid json"), "application/json")},
    )
    assert resp.status_code == 400
