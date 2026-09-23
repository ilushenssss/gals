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
        # «Стена» БПЗ поперёк всего пути от ВПП-1 до области облёта: растянута
        # по широте намного дальше, чем ищет локальный A* (visibility.find_path),
        # поэтому обхода заведомо нет и нарушение остаётся неустранимым.
        # Маленькую зону расчёт теперь обходит сам — на ней нарушения не будет.
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


def _upload_fleet(client, n=1, model="geoscan-gemini", status="Готов", name="Парк"):
    records = [
        {"inventory_number": f"{model}-{i}", "model": model, "status": status,
         "location_lat": 55.705, "location_lon": 37.56}
        for i in range(n)
    ]
    data = json.dumps(records).encode("utf-8")
    resp = client.post(
        "/api/fleets", data={"name": name},
        files={"file": ("fleet.json", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _default_fleet_id(client):
    """Парк по умолчанию — единственный загруженный тестом.

    Задача обязана называть парк, но большинству тестов этих модулей всё равно
    какой: они проверяют расчёт, а не выбор парка. Кому важно — передаёт
    fleet_id явно.
    """
    fleets = client.get("/api/fleets").json()
    assert fleets, "перед созданием задачи нужно загрузить парк (_upload_fleet)"
    return fleets[0]["id"]


def _create_task(client, env_id, fleet_id=None, **overrides):
    form = {
        "name": "Задача 1",
        "environment_id": env_id,
        "fleet_id": fleet_id or _default_fleet_id(client),
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


def _make_plan(client, with_launch_site=True, no_fly=False, n_fleet=2, **task_overrides):
    env_id = _upload_environment(client, with_launch_site=with_launch_site, no_fly=no_fly)
    _upload_fleet(client, n=n_fleet)
    task_id = _create_task(client, env_id, **task_overrides)
    plan = client.post("/api/plans", data={"task_id": task_id}).json()
    return plan["id"]


def test_safety_check_happy_path_passes(client):
    # n_fleet=1: несколько БВС на одном ВПП стартуют по расписанию без учета
    # разведения (ПЛН — известное v1-ограничение) и вполне могут оказаться в
    # одной точке в один момент — с одним БВС такого конфликта не возникает.
    # gsd_cm=2.5 (не дефолтный 3.0): на камере pf1b дефолтный GSD дает высоту
    # съемки 153.2 м — уже выше нового потолка 150 м (см. test_max_altitude_*
    # в test_api_safety.py) сам по себе, что здесь не проверяется.
    plan_id = _make_plan(client, n_fleet=1, gsd_cm="2.5")
    resp = client.post("/api/safety-checks", data={"plan_id": plan_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_id"] == plan_id
    names = {c["name"] for c in body["checks"]}
    assert names == {
        "geozones", "airspace", "altitude", "energy", "reachability", "coverage", "daylight", "separation",
    }
    assert body["status"] == "Пройдена"
    assert all(c["passed"] for c in body["checks"])
    assert body["auto_recalc_count"] == 0


def test_safety_check_detects_altitude_ceiling_violation(client):
    # Дефолтный gsd_cm=3.0 на камере pf1b (geoscan-gemini) дает высоту съемки
    # 153.2 м — выше потолка 150 м; расчетное ядро детерминировано, поэтому
    # ни один из трех автопересчетов это не исправит (высота не зависит от
    # версии плана, только от GSD и камеры).
    plan_id = _make_plan(client, n_fleet=1)
    resp = client.post("/api/safety-checks", data={"plan_id": plan_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    altitude = next(c for c in body["checks"] if c["name"] == "altitude")
    assert altitude["passed"] is False
    assert altitude["violations"]
    assert "153" in altitude["violations"][0]["message"]
    assert "150" in altitude["violations"][0]["message"]
    assert body["status"] == "Есть нарушения"
    assert body["auto_recalc_count"] == 3


def test_safety_check_detects_unavoidable_no_fly_zone_violation(client):
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
        v["message"].startswith(f"{uav_id} · вылет ")
        for v in geozones["violations"]
        if not v["message"].startswith("...")
    ), geozones["violations"]


def test_violations_carry_wgs84_coordinates_for_map_markers(client):
    # Каждое нарушение "геозон" — точка на реальном маршруте, поэтому у нее
    # должны быть координаты (для отметки на карте и подсветки, ИНТ.ФТ.14-15).
    plan_id = _make_plan(client, no_fly=True)
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    geozones = next(c for c in report["checks"] if c["name"] == "geozones")
    assert geozones["violations"]
    for v in geozones["violations"]:
        if v["message"].startswith("..."):
            continue  # сводная строка "...и еще N нарушений" — без точки
        assert v["lat"] is not None and v["lon"] is not None
        assert 54.0 < v["lat"] < 57.0  # в разумных пределах сцены (широта)
        assert 37.0 < v["lon"] < 38.0  # долгота


def test_astar_avoids_small_no_fly_zone_so_safety_check_passes(client):
    # Небольшая БПЗ прямо на пути от ВПП к области облета — в отличие от
    # непроходимой "стены" выше, ее можно обойти локальным A*
    # (uav_planner.visibility.find_path), и независимая проверка не должна
    # находить нарушения геозон.
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
        {
            "type": "Feature",
            "properties": {"layer": "no_fly", "safety_buffer_m": 0},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.570, 55.7045, 0.003, 0.002)},
        },
    ]
    scene = {"type": "FeatureCollection", "features": features}
    resp = client.post(
        "/api/environments",
        data={"name": "Обстановка с малой БПЗ"},
        files={"file": ("scene.geojson", io.BytesIO(json.dumps(scene).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    env_id = resp.json()["id"]

    fleet_id = _upload_fleet(client, n=1)
    # gsd_cm=2.5 — см. комментарий в test_safety_check_happy_path_passes:
    # дефолтный 3.0 на камере pf1b дает высоту 153.2 м, выше потолка 150 м.
    task_id = _create_task(client, env_id, fleet_id, gsd_cm="2.5")
    plan = client.post("/api/plans", data={"task_id": task_id}).json()
    detail = client.get(f"/api/plans/{plan['id']}").json()
    assert detail["warnings"] == []

    report = client.post("/api/safety-checks", data={"plan_id": plan["id"]}).json()
    geozones = next(c for c in report["checks"] if c["name"] == "geozones")
    assert geozones["passed"] is True
    assert report["status"] == "Пройдена"


def _first_violation(report):
    for c in report["checks"]:
        for v in c["violations"]:
            if not v["message"].startswith("..."):
                return v
    raise AssertionError("в отчёте нет нарушений")


def test_violations_each_get_a_stable_id(client):
    plan_id = _make_plan(client, no_fly=True)
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    v = _first_violation(report)
    assert v["id"]
    assert v["ignored"] is False
    assert report["violations_acknowledged"] is False


def test_ignoring_one_of_several_violations_does_not_acknowledge_the_report(client):
    plan_id = _make_plan(client, no_fly=True)
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    all_violation_ids = [
        v["id"] for c in report["checks"] for v in c["violations"] if not v["message"].startswith("...")
    ]
    assert len(all_violation_ids) >= 1

    updated = client.post(
        f"/api/safety-checks/{report['id']}/violations/{all_violation_ids[0]}/ignore",
        data={"ignored": "true"},
    ).json()
    if len(all_violation_ids) > 1:
        assert updated["violations_acknowledged"] is False
    else:
        assert updated["violations_acknowledged"] is True


def test_ignoring_all_violations_acknowledges_the_report(client):
    # Включая сводную строку "...и еще N нарушени(й)", если она есть — это тоже
    # отдельная запись violations с собственным id, ее тоже нужно принять явно.
    plan_id = _make_plan(client, no_fly=True)
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    all_violation_ids = [v["id"] for c in report["checks"] for v in c["violations"]]

    updated = report
    for vid in all_violation_ids:
        resp = client.post(
            f"/api/safety-checks/{report['id']}/violations/{vid}/ignore", data={"ignored": "true"},
        )
        assert resp.status_code == 200, resp.text
        updated = resp.json()

    assert updated["violations_acknowledged"] is True
    for c in updated["checks"]:
        for v in c["violations"]:
            assert v["ignored"] is True

    # Снятие галочки с одного нарушения снова блокирует подтверждение отчета.
    unignored = client.post(
        f"/api/safety-checks/{report['id']}/violations/{all_violation_ids[0]}/ignore",
        data={"ignored": "false"},
    ).json()
    assert unignored["violations_acknowledged"] is False

    # Возвращаем как было, чтобы get_latest_report тоже отражал финальное состояние.
    client.post(
        f"/api/safety-checks/{report['id']}/violations/{all_violation_ids[0]}/ignore",
        data={"ignored": "true"},
    )
    fetched = client.get("/api/safety-checks/latest", params={"plan_id": report["plan_id"]}).json()
    assert fetched["violations_acknowledged"] is True


def test_ignoring_unknown_violation_returns_404(client):
    plan_id = _make_plan(client, no_fly=True)
    report = client.post("/api/safety-checks", data={"plan_id": plan_id}).json()
    resp = client.post(
        f"/api/safety-checks/{report['id']}/violations/does-not-exist/ignore", data={"ignored": "true"},
    )
    assert resp.status_code == 404


def test_ignoring_violation_of_unknown_report_returns_404(client):
    resp = client.post(
        "/api/safety-checks/does-not-exist/violations/geozones__0/ignore", data={"ignored": "true"},
    )
    assert resp.status_code == 404
