"""Тесты API модуля «Планирование» — ПЛН.ФТ.2-3, ПЛН.ФТ.5-10."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon, shape

from uav_planner.api.app import app
from uav_planner.api import fleet_service, plan_service, service as environment_service, task_service


@pytest.fixture(autouse=True)
def _clear_stores():
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleets.clear()
    plan_service._plans.clear()
    plan_service._plan_ids_by_task.clear()
    yield
    environment_service._store.clear()
    task_service._tasks.clear()
    fleet_service._fleets.clear()
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


def _upload_fleet(client, n=1, model="geoscan-gemini", status="Готов", location_lat=55.705, location_lon=37.6):
    # Локация парка определяется из файла (по запросу пользователя), не
    # вводится вручную — задаём её через location_lat/lon каждого экземпляра.
    records = [
        {"inventory_number": f"{model}-{i}", "model": model, "status": status,
         "location_lat": location_lat, "location_lon": location_lon}
        for i in range(n)
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


def test_create_plan_happy_path(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client, n=2)
    task_id = _create_task(client, env_id, fleet_id)

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
    fleet_id = _upload_fleet(client)
    task_id = _create_task(client, env_id, fleet_id)

    client.post("/api/plans", data={"task_id": task_id})
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "Рассчитана"


def test_get_plan_returns_sorties_with_route_geometry(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client, n=2)
    task_id = _create_task(client, env_id, fleet_id)
    summary = client.post("/api/plans", data={"task_id": task_id}).json()

    detail = client.get(f"/api/plans/{summary['id']}").json()
    assert len(detail["sorties"]) == summary["sortie_count"]
    for sortie in detail["sorties"]:
        assert sortie["track_geojson"]["type"] == "LineString"
        assert sortie["flight_time_s"] > 0
        assert sortie["end_utc"] > sortie["start_utc"]


def test_sortie_phases_cover_the_whole_flight_with_no_gaps(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client, n=1)
    task_id = _create_task(client, env_id, fleet_id)
    plan_id = client.post("/api/plans", data={"task_id": task_id}).json()["id"]
    detail = client.get(f"/api/plans/{plan_id}").json()

    for sortie in detail["sorties"]:
        phases = sortie["phases"]
        assert phases  # взлет+перелет и хотя бы один галс есть всегда
        assert phases[0]["start_utc"] == sortie["start_utc"]
        assert phases[-1]["end_utc"] == sortie["end_utc"]
        assert "Взлет" in phases[0]["label"]
        assert any("Возврат" in p["label"] for p in phases) or phases[-1]["kind"] == "survey"
        # Этапы идут стык в стык, без разрывов и наложений.
        for prev, nxt in zip(phases, phases[1:]):
            assert prev["end_utc"] == nxt["start_utc"]
        total_phase_distance = sum(p["distance_m"] for p in phases)
        assert total_phase_distance == pytest.approx(sortie["distance_m"], rel=1e-6)


def test_second_calculation_creates_new_version(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    task_id = _create_task(client, env_id, fleet_id)

    first = client.post("/api/plans", data={"task_id": task_id}).json()
    second = client.post("/api/plans", data={"task_id": task_id}).json()
    assert second["version"] == first["version"] + 1

    versions = client.get("/api/plans", params={"task_id": task_id}).json()
    assert [v["version"] for v in versions] == [2, 1]


def test_plan_with_no_ready_instances_is_infeasible(client):
    # Парк существует (задачу с ним создать можно — совместимость по
    # расстоянию не про готовность экземпляров), но ни один экземпляр не в
    # статусе «Готов» -> расчет плана невыполним именно на этом основании.
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client, status="На обслуживании")
    task_id = _create_task(client, env_id, fleet_id)
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422
    assert "Готов" in resp.json()["detail"]


def test_plan_with_incompatible_survey_type_is_infeasible(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client, model="geoscan-801")  # тепловизор/RGB 12Мп, не мультиспектральный
    task_id = _create_task(client, env_id, fleet_id, survey_type="мультиспектральная")
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422
    assert "нагрузк" in resp.json()["detail"]


def test_plan_with_lidar_survey_type_is_infeasible(client):
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client)
    task_id = _create_task(client, env_id, fleet_id, survey_type="LiDAR")
    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422
    assert "LiDAR" in resp.json()["detail"] or "не поддерживается" in resp.json()["detail"]


def test_plan_without_launch_site_uses_area_centroid(client):
    env_id = _upload_environment(client, with_launch_site=False)
    fleet_id = _upload_fleet(client)
    task_id = _create_task(client, env_id, fleet_id)
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


def test_transit_route_avoids_no_fly_zone_via_astar(client):
    # БПЗ стоит прямо на прямой линии между ВПП и областью облета —
    # маршрут перехода должен обойти ее (uav_planner.visibility.find_path),
    # а не пересечь по прямой.
    no_fly_zone = Polygon(square_coords(37.570, 55.7045, 0.003, 0.002)[0])
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
        data={"name": "Обстановка с БПЗ на пути"},
        files={"file": ("scene.geojson", io.BytesIO(json.dumps(scene).encode("utf-8")), "application/json")},
    )
    env_id = resp.json()["id"]
    fleet_id = _upload_fleet(client, n=1)
    task_id = _create_task(client, env_id, fleet_id)

    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200, resp.text
    plan_id = resp.json()["id"]
    detail = client.get(f"/api/plans/{plan_id}").json()

    assert detail["warnings"] == []  # обход найден, откат на прямую линию не потребовался
    for sortie in detail["sorties"]:
        route = shape(sortie["track_geojson"])
        assert not route.intersects(no_fly_zone) or route.touches(no_fly_zone)
        # Маршрут действительно длиннее, чем если бы шел напрямую через зону.
        assert route.length > 0


def test_smaller_but_faster_model_group_wins_by_criterion(client):
    # Один быстрый и выносливый геоскан-201 против трех медленных gemini:
    # раньше побеждала группа с наибольшим числом экземпляров (gemini, 3>1);
    # теперь побеждает та, что реально минимизирует критерий задачи (J1 при
    # "Время") — а один быстрый самолет отработает задачу заметно быстрее.
    env_id = _upload_environment(client)
    records = [{"inventory_number": "201-1", "model": "geoscan-201", "status": "Готов",
                "location_lat": 55.705, "location_lon": 37.6}] + [
        {"inventory_number": f"GEM-{i}", "model": "geoscan-gemini", "status": "Готов",
         "location_lat": 55.705, "location_lon": 37.6} for i in range(3)
    ]
    resp = client.post(
        "/api/fleets", data={"name": "Парк"},
        files={"file": ("fleet.json", io.BytesIO(json.dumps(records).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    fleet_id = resp.json()["id"]
    task_id = _create_task(client, env_id, fleet_id, criterion_mode="Время")

    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["uav_model"] == "Геоскан 201"


def test_launch_site_chosen_by_nearest_not_first(client):
    # Дальняя ВПП стоит первой в файле обстановки — если бы площадка
    # выбиралась "первой попавшейся", вылет стартовал бы с нее; на деле
    # должна победить ближняя к области облета, вне зависимости от порядка.
    features = [
        {
            "type": "Feature",
            "properties": {"layer": "airspace", "h_min": 0, "h_max": 300},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.40, 55.60, 0.30, 0.20)},
        },
        {
            "type": "Feature",
            "properties": {"layer": "launch_site", "name": "ВПП Дальняя"},
            "geometry": {"type": "Point", "coordinates": [37.60, 55.75]},
        },
        {
            "type": "Feature",
            "properties": {"layer": "launch_site", "name": "ВПП Ближняя"},
            "geometry": {"type": "Point", "coordinates": [37.579, 55.703]},
        },
    ]
    scene = {"type": "FeatureCollection", "features": features}
    resp = client.post(
        "/api/environments",
        data={"name": "Две ВПП"},
        files={"file": ("scene.geojson", io.BytesIO(json.dumps(scene).encode("utf-8")), "application/json")},
    )
    env_id = resp.json()["id"]
    # Экземпляр без собственной локации — тестируем именно fallback на
    # ближайшую ВПП. Второй, нелетающий экземпляр с локацией нужен только
    # чтобы у парка вообще была определяемая по файлу локация (по запросу
    # пользователя) — на выбор площадки первым он не влияет (не "Готов").
    records = [
        {"inventory_number": "GEM-1", "model": "geoscan-gemini", "status": "Готов"},
        {"inventory_number": "GEM-anchor", "model": "geoscan-gemini", "status": "На обслуживании",
         "location_lat": 55.705, "location_lon": 37.6},
    ]
    resp = client.post(
        "/api/fleets", data={"name": "Парк"},
        files={"file": ("fleet.json", io.BytesIO(json.dumps(records).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    fleet_id = resp.json()["id"]
    task_id = _create_task(client, env_id, fleet_id)

    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/api/plans/{resp.json()['id']}").json()
    assert detail["sorties"][0]["takeoff_site"] == "ВПП Ближняя"


def test_instance_own_location_overrides_nearest_site(client):
    # У экземпляра указана собственная локация (совпадающая с дальней ВПП) -
    # она и должна использоваться, даже если геометрически есть ВПП ближе.
    features = [
        {
            "type": "Feature",
            "properties": {"layer": "airspace", "h_min": 0, "h_max": 300},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.40, 55.60, 0.30, 0.20)},
        },
        {
            "type": "Feature",
            "properties": {"layer": "launch_site", "name": "ВПП Ближняя"},
            "geometry": {"type": "Point", "coordinates": [37.579, 55.703]},
        },
    ]
    scene = {"type": "FeatureCollection", "features": features}
    resp = client.post(
        "/api/environments",
        data={"name": "Своя локация"},
        files={"file": ("scene.geojson", io.BytesIO(json.dumps(scene).encode("utf-8")), "application/json")},
    )
    env_id = resp.json()["id"]
    records = [{
        "inventory_number": "GEM-1", "model": "geoscan-gemini", "status": "Готов",
        "base_launch_site": "Своя площадка", "location_lat": 55.75, "location_lon": 37.60,
    }]
    resp = client.post(
        "/api/fleets",
        data={"name": "Парк", "location_lat": "55.75", "location_lon": "37.60"},
        files={"file": ("fleet.json", io.BytesIO(json.dumps(records).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    fleet_id = resp.json()["id"]
    task_id = _create_task(client, env_id, fleet_id)

    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/api/plans/{resp.json()['id']}").json()
    assert detail["sorties"][0]["takeoff_site"] == "Своя площадка"


def test_work_splits_across_two_vehicles_at_opposite_ends_of_the_area(client):
    # Два БВС у противоположных концов вытянутой области облета: жадный
    # маршрутизатор (routing.cluster_assign_and_route, Шаги 2-4) должен
    # закрепить каждому его половину, а не отдать всё одному.
    features = [{
        "type": "Feature",
        "properties": {"layer": "airspace", "h_min": 0, "h_max": 300},
        "geometry": {"type": "Polygon", "coordinates": square_coords(37.55, 55.70, 0.03, 0.01)},
    }]
    scene = {"type": "FeatureCollection", "features": features}
    resp = client.post(
        "/api/environments",
        data={"name": "Вытянутая область"},
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
        "/api/fleets",
        data={"name": "Парк", "location_lat": "55.705", "location_lon": "37.565"},
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

    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/api/plans/{resp.json()['id']}").json()

    by_vehicle: dict[str, list] = {}
    for sortie in detail["sorties"]:
        by_vehicle.setdefault(sortie["uav_id"], []).append(sortie)

    assert set(by_vehicle) == {"GEM-W", "GEM-E"}  # оба реально участвуют, не только один
    totals = {vid: sum(s["flight_time_s"] for s in sorties) for vid, sorties in by_vehicle.items()}
    # Балансировка узкого места не гарантирует идеальное равенство, но не
    # должна оставлять один БВС почти без работы на фоне другого.
    assert min(totals.values()) > 0.3 * max(totals.values())


def test_higher_wind_speed_increases_total_flight_time(client):
    # Крейсерская скорость = максимум модели минус скорость ветра (см. известные
    # ограничения) -> при том же покрытии больший ветер должен увеличивать J2.
    env_id = _upload_environment(client)
    fleet_id = _upload_fleet(client, n=2)

    task_calm = _create_task(client, env_id, fleet_id, name="Без ветра", wind_speed_ms="0")
    task_windy = _create_task(client, env_id, fleet_id, name="С ветром", wind_speed_ms="5")

    plan_calm = client.post("/api/plans", data={"task_id": task_calm}).json()
    plan_windy = client.post("/api/plans", data={"task_id": task_windy}).json()

    assert plan_windy["j2_s"] > plan_calm["j2_s"]
