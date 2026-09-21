"""Тесты API модуля «Проверка безопасности» — БЕЗ.ФТ.1-6."""

import io
import json

def square_coords(x0, y0, w, h):
    return [[[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h], [x0, y0]]]


def _upload_environment(client, with_launch_site=True, no_fly=False):
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
    if no_fly:
        # Небольшая БПЗ прямо посередине области облета, задаваемой в _create_task ниже.
        features.append({
            "type": "Feature",
            "properties": {"layer": "no_fly", "safety_buffer_m": 0},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.581, 55.7025, 0.003, 0.002)},
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


def _make_plan(client, with_launch_site=True, no_fly=False, n_fleet=2):
    env_id = _upload_environment(client, with_launch_site=with_launch_site, no_fly=no_fly)
    _upload_fleet(client, n=n_fleet)
    task_id = _create_task(client, env_id)
    plan = client.post("/api/plans", data={"task_id": task_id}).json()
    return plan["id"]


def test_safety_check_happy_path_passes(client):
    # n_fleet=1: несколько БВС на одном ВПП стартуют по расписанию без учета
    # разведения (ПЛН — известное v1-ограничение) и вполне могут оказаться в
    # одной точке в один момент — с одним БВС такого конфликта не возникает.
    plan_id = _make_plan(client, n_fleet=1)
    resp = client.post("/api/safety-checks", data={"plan_id": plan_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_id"] == plan_id
    names = {c["name"] for c in body["checks"]}
    assert names == {"geozones", "airspace", "energy", "reachability", "coverage", "daylight", "separation"}
    assert body["status"] == "Пройдена"
    assert all(c["passed"] for c in body["checks"])
    assert body["auto_recalc_count"] == 0


def test_safety_check_detects_no_fly_zone_violation(client):
    plan_id = _make_plan(client, no_fly=True)
    resp = client.post("/api/safety-checks", data={"plan_id": plan_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    geozones = next(c for c in body["checks"] if c["name"] == "geozones")
    assert geozones["passed"] is False
    assert geozones["violations"]
    assert body["status"] == "Есть нарушения"


def test_safety_check_exhausts_auto_recalc_limit_on_persistent_violation(client):
    # Нарушение геозоны определяется обстановкой и задачей, не версией плана
    # -> детерминированный пересчет воспроизводит его снова, и все три
    # автоматические попытки (БЕЗ.ФТ.3) расходуются в одном вызове.
    plan_id = _make_plan(client, no_fly=True)
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    assert report["auto_recalc_count"] == 3
    assert report["status"] == "Есть нарушения"
    # Отчет привязан к последней (пересчитанной) версии плана, не к исходной.
    versions = client.get("/api/plans", params={"task_id": report["task_id"]}).json()
    assert len(versions) == 4  # исходный план + 3 пересчета
    assert report["plan_id"] == versions[0]["id"]  # первый в списке — самый новый


def test_recheck_does_not_trigger_recalculation(client):
    plan_id = _make_plan(client, no_fly=True)
    first = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    assert first["auto_recalc_count"] == 3

    plans_before = client.get("/api/plans", params={"task_id": first["task_id"]}).json()
    rechecked = client.post("/api/safety-checks/recheck", data={"plan_id": first["plan_id"]}).json()
    plans_after = client.get("/api/plans", params={"task_id": first["task_id"]}).json()

    assert len(plans_after) == len(plans_before)  # пересчета не было
    assert rechecked["plan_id"] == first["plan_id"]
    assert rechecked["auto_recalc_count"] == 3
    assert rechecked["status"] == "Есть нарушения"


def test_get_latest_safety_check(client):
    plan_id = _make_plan(client)
    created = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    fetched = client.get("/api/safety-checks/latest", params={"plan_id": plan_id}).json()
    assert fetched["id"] == created["id"]


def test_get_latest_safety_check_without_prior_run_returns_404(client):
    plan_id = _make_plan(client)
    resp = client.get("/api/safety-checks/latest", params={"plan_id": plan_id})
    assert resp.status_code == 404


def test_safety_check_for_unknown_plan_returns_404(client):
    resp = client.post("/api/safety-checks", data={"plan_id": "does-not-exist"})
    assert resp.status_code == 404


def test_safety_check_reachability_fails_without_landing_site(client):
    plan_id = _make_plan(client, with_launch_site=False)
    resp = client.post("/api/safety-checks", data={"plan_id": plan_id})
    body = resp.json()
    reachability = next(c for c in body["checks"] if c["name"] == "reachability")
    assert reachability["passed"] is False


def test_violation_names_the_uav_and_sortie(client):
    """БЕЗ.ФТ.4: при нарушении показывается идентификатор БВС и вылета.

    Чистые проверки идентификаторов не знают — их приписывает оркестратор,
    единственный, кто видит, чей это вылет. Это же делает нарушение
    адресуемым на карте (ИНТ.ФТ.14).
    """
    plan_id = _make_plan(client, no_fly=True, n_fleet=1)
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()

    geozones = next(c for c in report["checks"] if c["name"] == "geozones")
    assert geozones["passed"] is False
    plan = client.get(f"/api/plans/{report['plan_id']}").json()
    uav_id = plan["sorties"][0]["uav_id"]
    assert all(
        v.startswith(f"{uav_id} · вылет ") for v in geozones["violations"] if not v.startswith("...")
    ), geozones["violations"]
