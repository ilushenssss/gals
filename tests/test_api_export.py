"""Тесты API модуля «Подтверждение и экспорт» — ЭКС.ФТ.2-3, ЭКС.ФТ.5-9."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape

from uav_planner.api.app import app
from uav_planner.api import (
    fleet_service,
    plan_service,
    safety_service,
    service as environment_service,
    task_service,
)


@pytest.fixture(autouse=True)
def _clear_stores():
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleets.clear()
    plan_service._plans.clear()
    plan_service._plan_ids_by_task.clear()
    safety_service._reports_by_plan.clear()
    safety_service._reports_by_id.clear()
    safety_service._attempts_by_task_version.clear()
    yield
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleets.clear()
    plan_service._plans.clear()
    plan_service._plan_ids_by_task.clear()
    safety_service._reports_by_plan.clear()
    safety_service._reports_by_id.clear()
    safety_service._attempts_by_task_version.clear()


@pytest.fixture
def client():
    return TestClient(app)


def square_coords(x0, y0, w, h):
    return [[[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h], [x0, y0]]]


def _upload_environment(client, no_fly=False):
    features = [
        {
            "type": "Feature",
            "properties": {"layer": "airspace", "h_min": 0, "h_max": 300},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.55, 55.70, 0.10, 0.01)},
        },
        {
            "type": "Feature",
            "properties": {"layer": "launch_site", "name": "ВПП-1"},
            "geometry": {"type": "Point", "coordinates": [37.56, 55.705]},
        },
    ]
    if no_fly:
        # "Стена" поперек всего пути от ВПП до области — непроходимая для A*,
        # чтобы проверка безопасности стабильно находила нарушение.
        features.append({
            "type": "Feature",
            "properties": {"layer": "no_fly", "safety_buffer_m": 0},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.565, 55.0, 0.01, 1.4)},
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


def _upload_fleet(client, n=1, model="geoscan-gemini", location_lat=55.705, location_lon=37.56):
    # Реальные экземпляры намеренно без собственной локации — здесь важен
    # взлёт с ближайшей ВПП обстановки (в т.ч. непроходимая "стена" БПЗ на
    # пути к ней), а не собственная локация экземпляра. Локация парка (по
    # запросу пользователя теперь определяется из файла, не вводится
    # вручную) — с отдельного нелетающего "якорного" экземпляра.
    records = [{"inventory_number": f"{model}-{i}", "model": model, "status": "Готов"} for i in range(n)] + [
        {"inventory_number": "anchor", "model": model, "status": "На обслуживании",
         "location_lat": location_lat, "location_lon": location_lon},
    ]
    data = json.dumps(records).encode("utf-8")
    resp = client.post(
        "/api/fleets", data={"name": "Парк"},
        files={"file": ("fleet.json", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_task(client, env_id, fleet_id, **overrides):
    form = {
        "name": "Задача 1",
        "environment_id": env_id,
        "fleet_id": fleet_id,
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


def _make_reviewed_plan(client, n_fleet=1, no_fly=False):
    """Полный путь Черновик -> Проверен (без нарушений)."""
    env_id = _upload_environment(client, no_fly=no_fly)
    fleet_id = _upload_fleet(client, n=n_fleet)
    task_id = _create_task(client, env_id, fleet_id)
    plan_id = client.post("/api/plans", data={"task_id": task_id}).json()["id"]
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    # БЕЗ.ФТ.3: при нарушении план мог быть автоматически пересчитан — реальный id другой.
    return report["plan_id"], task_id


def test_plan_starts_as_draft(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    task_id = _create_task(client, env_id, fleet_id)
    plan = client.post("/api/plans", data={"task_id": task_id}).json()
    assert plan["status"] == "Черновик"


def test_plan_becomes_reviewed_after_passing_safety_check(client):
    plan_id, _ = _make_reviewed_plan(client)
    detail = client.get(f"/api/plans/{plan_id}").json()
    assert detail["status"] == "Проверен"


def test_plan_with_violations_stays_draft_and_cannot_be_confirmed(client):
    plan_id, _ = _make_reviewed_plan(client, no_fly=True)
    detail = client.get(f"/api/plans/{plan_id}").json()
    assert detail["status"] == "Черновик"

    resp = client.post(f"/api/plans/{plan_id}/confirm", data={})
    assert resp.status_code == 409, resp.text


def test_confirming_with_some_but_not_all_violations_ignored_still_fails(client):
    plan_id, _ = _make_reviewed_plan(client, no_fly=True)
    report = client.get("/api/safety-checks/latest", params={"plan_id": plan_id}).json()
    violation_ids = [v["id"] for c in report["checks"] for v in c["violations"]]
    assert len(violation_ids) >= 1

    if len(violation_ids) > 1:
        client.post(
            f"/api/safety-checks/{report['id']}/violations/{violation_ids[0]}/ignore",
            data={"ignored": "true"},
        )
        resp = client.post(f"/api/plans/{plan_id}/confirm", data={})
        assert resp.status_code == 409, resp.text


def test_confirming_with_all_violations_ignored_overrides_the_block(client):
    # Пользовательское расширение поверх ЭКС.ФТ.2: если оператор явно отметил
    # ВСЕ нарушения последнего отчета принятыми, план все равно можно
    # подтвердить и экспортировать, несмотря на статус "Черновик".
    plan_id, _ = _make_reviewed_plan(client, no_fly=True)
    report = client.get("/api/safety-checks/latest", params={"plan_id": plan_id}).json()
    violation_ids = [v["id"] for c in report["checks"] for v in c["violations"]]
    assert violation_ids

    for vid in violation_ids:
        resp = client.post(
            f"/api/safety-checks/{report['id']}/violations/{vid}/ignore", data={"ignored": "true"},
        )
        assert resp.status_code == 200, resp.text
    assert resp.json()["violations_acknowledged"] is True

    confirm_resp = client.post(f"/api/plans/{plan_id}/confirm", data={"confirmed_by": "Сидоров С.С."})
    assert confirm_resp.status_code == 200, confirm_resp.text
    body = confirm_resp.json()
    assert body["status"] == "Подтвержден"
    assert body["confirmed_with_overrides"] is True
    assert body["confirmed_by"] == "Сидоров С.С."

    # Экспорт работает как обычно после такого подтверждения.
    plan = client.get(f"/api/plans/{plan_id}").json()
    uav_id = plan["sorties"][0]["uav_id"]
    export_resp = client.get(f"/api/plans/{plan_id}/export/geojson/{uav_id}")
    assert export_resp.status_code == 200, export_resp.text


def test_unignoring_a_violation_after_confirming_does_not_retroactively_unconfirm(client):
    # Отметка "принято" снятая после подтверждения не откатывает уже
    # подтвержденный план — ЭКС.ФТ.3: подтвержденный план неизменен.
    plan_id, _ = _make_reviewed_plan(client, no_fly=True)
    report = client.get("/api/safety-checks/latest", params={"plan_id": plan_id}).json()
    violation_ids = [v["id"] for c in report["checks"] for v in c["violations"]]
    for vid in violation_ids:
        client.post(f"/api/safety-checks/{report['id']}/violations/{vid}/ignore", data={"ignored": "true"})
    client.post(f"/api/plans/{plan_id}/confirm", data={})

    client.post(
        f"/api/safety-checks/{report['id']}/violations/{violation_ids[0]}/ignore", data={"ignored": "false"},
    )
    detail = client.get(f"/api/plans/{plan_id}").json()
    assert detail["status"] == "Подтвержден"


def test_confirm_requires_prior_safety_check(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    task_id = _create_task(client, env_id, fleet_id)
    plan_id = client.post("/api/plans", data={"task_id": task_id}).json()["id"]

    resp = client.post(f"/api/plans/{plan_id}/confirm", data={})
    assert resp.status_code == 409


def test_confirm_plan_happy_path(client):
    plan_id, task_id = _make_reviewed_plan(client)

    resp = client.post(f"/api/plans/{plan_id}/confirm", data={"confirmed_by": "Иванов И.И."})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Подтвержден"
    assert body["confirmed_by"] == "Иванов И.И."
    assert body["confirmed_at"] is not None

    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "Подтверждена"


def test_second_confirmation_is_a_conflict(client):
    # ЭКС.ФТ.9: первый запрос подтверждает, второй получает отказ с указанием,
    # кем план уже подтвержден.
    plan_id, _ = _make_reviewed_plan(client)
    first = client.post(f"/api/plans/{plan_id}/confirm", data={"confirmed_by": "Иванов И.И."})
    assert first.status_code == 200

    second = client.post(f"/api/plans/{plan_id}/confirm", data={"confirmed_by": "Петров П.П."})
    assert second.status_code == 409
    assert "Иванов И.И." in second.json()["detail"]

    # Подтверждение осталось за первым пользователем.
    detail = client.get(f"/api/plans/{plan_id}").json()
    assert detail["confirmed_by"] == "Иванов И.И."


def test_confirming_task_blocks_task_editing(client):
    plan_id, task_id = _make_reviewed_plan(client)
    client.post(f"/api/plans/{plan_id}/confirm", data={})

    task = client.get(f"/api/tasks/{task_id}").json()
    area = {"type": "Polygon", "coordinates": square_coords(37.58, 55.702, 0.005, 0.004)}
    resp = client.put(
        f"/api/tasks/{task_id}",
        data={
            "expected_version": task["version"], "name": "Новое имя",
            "survey_type": "RGB", "gsd_cm": "3.0", "work_date": "2026-06-15",
            "criterion_mode": "Время",
        },
        files={"area_file": ("a.geojson", io.BytesIO(json.dumps(area).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 409


def test_export_blocked_before_confirmation(client):
    plan_id, _ = _make_reviewed_plan(client)
    resp = client.get(f"/api/plans/{plan_id}/export/kml/geoscan-gemini-0")
    assert resp.status_code == 409


def test_export_kml_and_geojson_after_confirmation(client):
    plan_id, _ = _make_reviewed_plan(client, n_fleet=1)
    client.post(f"/api/plans/{plan_id}/confirm", data={})
    plan = client.get(f"/api/plans/{plan_id}").json()
    uav_id = plan["sorties"][0]["uav_id"]

    kml_resp = client.get(f"/api/plans/{plan_id}/export/kml/{uav_id}")
    assert kml_resp.status_code == 200, kml_resp.text
    assert kml_resp.headers["content-type"].startswith("application/vnd.google-earth.kml+xml")
    kml_text = kml_resp.text
    assert "<kml" in kml_text
    assert "Маршрут" in kml_text
    assert "Галсы" in kml_text
    assert "<TimeSpan>" in kml_text
    assert "<TimeStamp>" in kml_text
    assert "relativeToGround" in kml_text

    geojson_resp = client.get(f"/api/plans/{plan_id}/export/geojson/{uav_id}")
    assert geojson_resp.status_code == 200, geojson_resp.text
    fc = geojson_resp.json()
    assert fc["type"] == "FeatureCollection"
    types = {f["properties"]["type"] for f in fc["features"]}
    assert types == {"Вылет", "Ключевая точка", "Галсы вылета", "Зона покрытия"}
    # ЭКС.ФТ.4: все координаты в WGS-84 c явной высотой [lon, lat, alt].
    for f in fc["features"]:
        coords = f["geometry"]["coordinates"]
        while isinstance(coords[0], list):
            coords = coords[0]
        assert len(coords) == 3
        lon, lat, _alt = coords
        assert 37.0 < lon < 38.0
        assert 54.0 < lat < 57.0

    # Первая успешная выгрузка переводит план в "Выгружен" (ЭКС.ФТ.5/7).
    plan_after = client.get(f"/api/plans/{plan_id}").json()
    assert plan_after["status"] == "Выгружен"
    assert plan_after["exported_at"] is not None


def test_export_unknown_uav_returns_404(client):
    plan_id, _ = _make_reviewed_plan(client)
    client.post(f"/api/plans/{plan_id}/confirm", data={})
    resp = client.get(f"/api/plans/{plan_id}/export/kml/does-not-exist")
    assert resp.status_code == 404


def test_export_zip_bundles_all_uavs(client):
    # Два экземпляра на разных площадках у противоположных концов вытянутой
    # области (как в test_api_plan.py::test_work_splits...) — маршрутизатор
    # закрепляет каждому свою половину, оба реально летают, и (в отличие от
    # старта с одной точки, см. test_api_safety.py про n_fleet=1) проверка
    # разведения не срабатывает.
    features = [
        {"type": "Feature", "properties": {"layer": "airspace", "h_min": 0, "h_max": 300},
         "geometry": {"type": "Polygon", "coordinates": square_coords(37.55, 55.70, 0.03, 0.01)}},
        {"type": "Feature", "properties": {"layer": "launch_site", "name": "ВПП Запад"},
         "geometry": {"type": "Point", "coordinates": [37.552, 55.705]}},
        {"type": "Feature", "properties": {"layer": "launch_site", "name": "ВПП Восток"},
         "geometry": {"type": "Point", "coordinates": [37.578, 55.705]}},
    ]
    scene = {"type": "FeatureCollection", "features": features}
    resp = client.post(
        "/api/environments", data={"name": "Обстановка с двумя площадками"},
        files={"file": ("scene.geojson", io.BytesIO(json.dumps(scene).encode("utf-8")), "application/json")},
    )
    env_id = resp.json()["id"]
    records = [
        {"inventory_number": "GEM-W", "model": "geoscan-gemini", "status": "Готов",
         "location_lat": 55.705, "location_lon": 37.552},
        {"inventory_number": "GEM-E", "model": "geoscan-gemini", "status": "Готов",
         "location_lat": 55.705, "location_lon": 37.578},
    ]
    resp = client.post(
        "/api/fleets", data={"name": "Парк"},
        files={"file": ("fleet.json", io.BytesIO(json.dumps(records).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    fleet_id = resp.json()["id"]

    area = {"type": "Polygon", "coordinates": square_coords(37.552, 55.702, 0.026, 0.004)}
    form = {
        "name": "Задача с двумя площадками", "environment_id": env_id, "fleet_id": fleet_id, "survey_type": "RGB",
        "gsd_cm": "3.0", "work_date": "2026-06-15", "criterion_mode": "Время",
    }
    resp = client.post(
        "/api/tasks", data=form,
        files={"area_file": ("area.geojson", io.BytesIO(json.dumps(area).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["id"]

    plan_id = client.post("/api/plans", data={"task_id": task_id}).json()["id"]
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    plan_id = report["plan_id"]
    assert report["status"] == "Пройдена", report

    confirm_resp = client.post(f"/api/plans/{plan_id}/confirm", data={})
    assert confirm_resp.status_code == 200, confirm_resp.text
    plan = client.get(f"/api/plans/{plan_id}").json()
    uav_ids = {s["uav_id"] for s in plan["sorties"]}
    assert uav_ids == {"GEM-W", "GEM-E"}

    resp = client.get(f"/api/plans/{plan_id}/export/zip")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        for uav_id in uav_ids:
            assert f"{uav_id}.kml" in names
            assert f"{uav_id}.geojson" in names


def test_confirm_unknown_plan_returns_404(client):
    resp = client.post("/api/plans/does-not-exist/confirm", data={})
    assert resp.status_code == 404


def test_export_routes_are_consistent_with_planned_track(client):
    # Сверяем, что экспортированный маршрут в KML геометрически совпадает
    # с посчитанным маршрутом вылета (в пределах округления координат).
    plan_id, _ = _make_reviewed_plan(client, n_fleet=1)
    client.post(f"/api/plans/{plan_id}/confirm", data={})
    plan = client.get(f"/api/plans/{plan_id}").json()
    sortie = plan["sorties"][0]
    planned_route = shape(sortie["track_geojson"])

    geojson_resp = client.get(f"/api/plans/{plan_id}/export/geojson/{sortie['uav_id']}").json()
    route_feature = next(f for f in geojson_resp["features"] if f["properties"]["type"] == "Вылет")
    exported_coords_2d = [(c[0], c[1]) for c in route_feature["geometry"]["coordinates"]]
    exported_route = shape({"type": "LineString", "coordinates": exported_coords_2d})
    assert exported_route.length == pytest.approx(planned_route.length, rel=1e-6)
