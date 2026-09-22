"""Тесты модуля «Подтверждение и экспорт» — ЭКС.ФТ.2-9.

Сквозная предпосылка всех тестов: план подтверждается только после проверки
безопасности без нарушений, поэтому сцена собрана так, чтобы проверка
проходила (один БВС — иначе срабатывает разведение, см. известное
v1-ограничение расписания).
"""

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import quote

KML_NS = {"k": "http://www.opengis.net/kml/2.2"}


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
        # «Стена» БПЗ поперёк всего пути от ВПП-1 до области облёта: растянута
        # по широте намного дальше, чем ищет локальный A* (visibility.find_path),
        # поэтому обхода заведомо нет и нарушение остаётся неустранимым.
        # Маленькую зону расчёт теперь обходит сам — на ней нарушения не будет.
        features.append({
            "type": "Feature",
            "properties": {"layer": "no_fly", "safety_buffer_m": 0},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.565, 55.0, 0.01, 1.4)},
        })
    data = json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")
    resp = client.post(
        "/api/environments",
        data={"name": "Обстановка"},
        files={"file": ("scene.geojson", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _upload_fleet(client, inventory_numbers=("gemini-0",), name="Парк"):
    records = [
        {"inventory_number": number, "model": "geoscan-gemini", "status": "Готов",
         "location_lat": 55.705, "location_lon": 37.56}
        for number in inventory_numbers
    ]
    data = json.dumps(records).encode("utf-8")
    resp = client.post(
        "/api/fleets", data={"name": name},
        files={"file": ("fleet.json", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _create_task(client, env_id):
    area = {"type": "Polygon", "coordinates": square_coords(37.58, 55.702, 0.005, 0.004)}
    resp = client.post(
        "/api/tasks",
        data={
            "name": "Задача 1", "environment_id": env_id,
            "fleet_id": client.get("/api/fleets").json()[0]["id"], "survey_type": "RGB",
            "gsd_cm": "3.0", "work_date": "2026-06-15", "criterion_mode": "Время",
        },
        files={"area_file": ("area.geojson", io.BytesIO(json.dumps(area).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _plan(client, no_fly=False, inventory_numbers=("gemini-0",)):
    env_id = _upload_environment(client, no_fly=no_fly)
    _upload_fleet(client, inventory_numbers)
    task_id = _create_task(client, env_id)
    return client.post("/api/plans", data={"task_id": task_id}).json()


def _checked_plan(client, no_fly=False, inventory_numbers=("gemini-0",)):
    plan = _plan(client, no_fly=no_fly, inventory_numbers=inventory_numbers)
    report = client.post("/api/safety-checks", data={"plan_id": plan["id"]}).json()
    return report["plan_id"], report


def _user_header(name):
    """ФИО кириллические, а заголовок обязан быть ASCII — шлем как корректный
    клиент, процентным кодированием UTF-8 (см. api/deps.py)."""
    return {"X-User-Name": quote(name)}


def _confirmed_plan(client, user="Иванов И. И.", inventory_numbers=("gemini-0",)):
    plan_id, report = _checked_plan(client, inventory_numbers=inventory_numbers)
    assert report["status"] == "Пройдена", report
    resp = client.post(f"/api/plans/{plan_id}/confirm", headers=_user_header(user))
    assert resp.status_code == 200, resp.text
    return plan_id


# --- статус плана, ЭКС.ФТ.5 -------------------------------------------------


def test_new_plan_is_a_draft(client):
    plan = _plan(client)
    assert plan["status"] == "Черновик"
    assert plan["confirmed_at"] is None


def test_safety_check_moves_plan_to_checked(client):
    plan_id, _ = _checked_plan(client)
    assert client.get(f"/api/plans/{plan_id}").json()["status"] == "Проверен"


def test_first_export_moves_plan_to_exported(client):
    plan_id = _confirmed_plan(client)
    assert client.get(f"/api/plans/{plan_id}").json()["status"] == "Подтвержден"

    client.get(f"/api/plans/{plan_id}/export", params={"format": "kml", "uav_id": "gemini-0"})
    plan = client.get(f"/api/plans/{plan_id}").json()
    assert plan["status"] == "Выгружен"
    assert plan["exported_at"] is not None


def test_recheck_does_not_roll_the_status_back(client):
    """ЭКС.ФТ.3: повторная проверка не должна откатывать жизненный цикл."""
    plan_id = _confirmed_plan(client)
    client.post("/api/safety-checks/recheck", data={"plan_id": plan_id})
    assert client.get(f"/api/plans/{plan_id}").json()["status"] == "Подтвержден"


# --- подтверждение, ЭКС.ФТ.2/ФТ.6/ФТ.9 --------------------------------------


def test_confirm_records_who_and_when(client):
    plan_id = _confirmed_plan(client, user="Петров П. П.")
    plan = client.get(f"/api/plans/{plan_id}").json()
    assert plan["status"] == "Подтвержден"
    assert plan["confirmed_by"] == "Петров П. П."
    assert plan["confirmed_at"] is not None


def test_confirm_without_safety_check_is_rejected(client):
    """ЭКС.ФТ.2: без выполненной проверки подтверждать нечего."""
    plan = _plan(client)
    resp = client.post(f"/api/plans/{plan['id']}/confirm")
    assert resp.status_code == 422
    assert "проверка безопасности еще не выполнялась" in resp.json()["detail"]


def test_confirm_with_violations_is_rejected(client):
    """ЭКС.ФТ.2: хотя бы одно нарушение — подтверждение запрещено."""
    plan_id, report = _checked_plan(client, no_fly=True)
    assert report["status"] == "Есть нарушения"

    resp = client.post(f"/api/plans/{plan_id}/confirm")
    assert resp.status_code == 422
    assert "нарушения" in resp.json()["detail"]
    assert client.get(f"/api/plans/{plan_id}").json()["status"] == "Проверен"


def test_second_confirm_reports_who_was_first(client):
    """ЭКС.ФТ.9: выигрывает первый, второй получает 409 с именем и статусом."""
    plan_id = _confirmed_plan(client, user="Иванов И. И.")

    resp = client.post(f"/api/plans/{plan_id}/confirm", headers=_user_header("Сидоров С. С."))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["message"] == "План уже подтвержден пользователем (Иванов И. И.)"
    assert detail["plan"]["status"] == "Подтвержден"
    assert detail["plan"]["confirmed_by"] == "Иванов И. И."


def test_raw_utf8_user_name_from_a_browser_is_repaired(client):
    """Браузер шлет ФИО сырыми октетами; после latin-1 они приезжают
    «кракозябрами» и обязаны восстанавливаться."""
    plan_id, _ = _checked_plan(client)
    # Именно сырые байты, как их кладет в заголовок браузер.
    client.post(
        f"/api/plans/{plan_id}/confirm",
        headers={"X-User-Name": "Иванов И. И.".encode("utf-8")},
    )
    assert client.get(f"/api/plans/{plan_id}").json()["confirmed_by"] == "Иванов И. И."


def test_confirm_defaults_the_user_name(client):
    """Заголовка нет — пишем «Оператор», а не пустоту: сообщение ЭКС.ФТ.9
    должно оставаться читаемым."""
    plan_id, _ = _checked_plan(client)
    client.post(f"/api/plans/{plan_id}/confirm")
    assert client.get(f"/api/plans/{plan_id}").json()["confirmed_by"] == "Оператор"


def test_confirm_unknown_plan_returns_404(client):
    assert client.post("/api/plans/нет-такого/confirm").status_code == 404


# --- экспорт, ЭКС.ФТ.4/ФТ.7/ФТ.8 --------------------------------------------


def test_export_is_blocked_until_the_plan_is_confirmed(client):
    plan_id, _ = _checked_plan(client)
    resp = client.get(f"/api/plans/{plan_id}/export", params={"format": "kml"})
    assert resp.status_code == 409
    assert "подтвердите" in resp.json()["detail"]


def test_kml_export_has_the_structure_required_by_the_spec(client):
    plan_id = _confirmed_plan(client)
    resp = client.get(
        f"/api/plans/{plan_id}/export", params={"format": "kml", "uav_id": "gemini-0"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.google-earth.kml+xml")

    root = ET.fromstring(resp.content)  # валидный XML — первое требование
    assert root.tag == "{http://www.opengis.net/kml/2.2}kml"

    document = root.find("k:Document", KML_NS)
    uav_folder = document.find("k:Folder", KML_NS)
    assert uav_folder.find("k:name", KML_NS).text == "БВС gemini-0"

    sortie_folder = uav_folder.find("k:Folder", KML_NS)
    assert sortie_folder.find("k:name", KML_NS).text.startswith("Вылет ")
    timespan = sortie_folder.find("k:TimeSpan", KML_NS)
    assert timespan.find("k:begin", KML_NS).text.endswith("Z")  # ISO 8601 UTC
    assert timespan.find("k:end", KML_NS).text.endswith("Z")

    names = [p.find("k:name", KML_NS).text for p in sortie_folder.findall("k:Placemark", KML_NS)]
    assert names[0] == "Маршрут"
    assert names[1] == "Галсы"
    assert any(n.startswith("Точка 1 — Взлет") for n in names)
    assert any("Посадка" in n for n in names)

    route = sortie_folder.findall("k:Placemark", KML_NS)[0]
    line = route.find("k:LineString", KML_NS)
    assert line.find("k:altitudeMode", KML_NS).text == "relativeToGround"
    assert sortie_folder.findall("k:Placemark", KML_NS)[1].find("k:MultiGeometry", KML_NS) is not None

    point = next(p for p in sortie_folder.findall("k:Placemark", KML_NS) if p.find("k:Point", KML_NS) is not None)
    assert point.find("k:TimeStamp/k:when", KML_NS).text.endswith("Z")
    fields = {d.get("name") for d in point.findall("k:ExtendedData/k:Data", KML_NS)}
    assert {"Этап", "Высота, м", "Скорость, м/с"} <= fields


def test_kml_coordinates_are_wgs84_lon_lat_alt(client):
    """ЭКС.ФТ.4: координаты — долгота, широта, высота в WGS-84."""
    plan_id = _confirmed_plan(client)
    content = client.get(f"/api/plans/{plan_id}/export", params={"format": "kml"}).content
    root = ET.fromstring(content)
    text = root.find(".//k:LineString/k:coordinates", KML_NS).text
    lon, lat, alt = (float(v) for v in text.split()[0].split(","))
    assert 37.5 < lon < 37.6  # долгота сцены
    assert 55.6 < lat < 55.8  # широта сцены
    assert alt >= 0


def test_geojson_export_contains_all_four_object_kinds(client):
    plan_id = _confirmed_plan(client)
    resp = client.get(
        f"/api/plans/{plan_id}/export", params={"format": "geojson", "uav_id": "gemini-0"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/geo+json")

    body = resp.json()
    assert body["type"] == "FeatureCollection"
    assert body["properties"]["crs"] == "WGS 84 (EPSG:4326)"

    objects = {f["properties"]["object"] for f in body["features"]}
    assert objects == {"Вылет", "Ключевая точка", "Галсы вылета", "Зона покрытия"}

    sortie = next(f for f in body["features"] if f["properties"]["object"] == "Вылет")
    assert sortie["geometry"]["type"] == "LineString"
    assert sortie["properties"]["start_utc"].endswith("Z")

    waypoint = next(f for f in body["features"] if f["properties"]["object"] == "Ключевая точка")
    assert len(waypoint["geometry"]["coordinates"]) == 3  # [lon, lat, alt]
    assert waypoint["properties"]["stage"] in {"Взлет", "Перелет", "Съемка", "Посадка"}

    coverage = next(f for f in body["features"] if f["properties"]["object"] == "Зона покрытия")
    assert coverage["geometry"]["type"] in {"Polygon", "MultiPolygon"}


def test_waypoint_times_run_from_takeoff_to_landing(client):
    """Время прохода интерполируется по длине и обязано быть монотонным."""
    plan_id = _confirmed_plan(client)
    body = client.get(f"/api/plans/{plan_id}/export", params={"format": "geojson"}).json()
    sortie = next(f for f in body["features"] if f["properties"]["object"] == "Вылет")
    points = [
        f for f in body["features"]
        if f["properties"]["object"] == "Ключевая точка"
        and f["properties"]["sortie_index"] == sortie["properties"]["sortie_index"]
    ]
    times = [f["properties"]["time_utc"] for f in points]
    assert times == sorted(times)
    assert times[0] == sortie["properties"]["start_utc"]
    assert times[-1] == sortie["properties"]["end_utc"]
    assert points[0]["properties"]["stage"] == "Взлет"
    assert points[-1]["properties"]["stage"] == "Посадка"


def test_export_for_unknown_uav_returns_404(client):
    plan_id = _confirmed_plan(client)
    resp = client.get(f"/api/plans/{plan_id}/export", params={"format": "kml", "uav_id": "нет"})
    assert resp.status_code == 404
    assert "нет вылетов" in resp.json()["detail"]


def test_download_all_returns_a_zip_with_both_formats_per_uav(client):
    plan_id = _confirmed_plan(client, inventory_numbers=("gemini-0",))
    resp = client.get(f"/api/plans/{plan_id}/export/all")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        names = sorted(archive.namelist())
        assert names == ["план-в1-gemini-0.geojson", "план-в1-gemini-0.kml"]
        ET.fromstring(archive.read("план-в1-gemini-0.kml"))
        json.loads(archive.read("план-в1-gemini-0.geojson"))


def test_cyrillic_filename_goes_out_as_rfc5987(client):
    """Кириллица в инвентарном номере не должна портиться браузером."""
    plan_id = _confirmed_plan(client, inventory_numbers=("Гемини-1",))
    resp = client.get(
        f"/api/plans/{plan_id}/export", params={"format": "kml", "uav_id": "Гемини-1"}
    )
    disposition = resp.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "%D0%93" in disposition  # «Г» в процентном кодировании
    assert 'filename="' in disposition  # запасной ASCII-вариант для старых клиентов


def test_export_is_journalled_and_repeatable(client):
    """Файл — чистая функция от неизменяемого плана, поэтому повтор совпадает."""
    plan_id = _confirmed_plan(client)
    first = client.get(f"/api/plans/{plan_id}/export", params={"format": "kml"}).content
    second = client.get(f"/api/plans/{plan_id}/export", params={"format": "kml"}).content
    assert first == second

    from uav_planner.models.plan import ExportArtifact
    from uav_planner.db.session import current_session

    rows = current_session().query(ExportArtifact).all()
    assert len(rows) == 2
    assert {r.format for r in rows} == {"kml"}


def test_older_version_is_not_confirmable_by_a_report_about_a_newer_one(client):
    """После автопересчета БЕЗ.ФТ.3 отчет относится к новой версии плана.

    Запрошенная версия при этом остается «Черновиком» и подтверждаться не
    должна — иначе оператор подтвердит план, который никто не проверял.
    """
    env_id = _upload_environment(client, no_fly=True)
    _upload_fleet(client)
    task_id = _create_task(client, env_id)
    first = client.post("/api/plans", data={"task_id": task_id}).json()

    report = client.post("/api/safety-checks", data={"plan_id": first["id"]}).json()
    assert report["auto_recalc_count"] == 3
    assert report["plan_id"] != first["id"]

    resp = client.post(f"/api/plans/{first['id']}/confirm")
    assert resp.status_code == 422
    assert "другой версии" in resp.json()["detail"]
    assert client.get(f"/api/plans/{first['id']}").json()["status"] == "Черновик"
