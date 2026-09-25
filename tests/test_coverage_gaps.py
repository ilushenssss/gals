"""Пути, которые аудит нашел непокрытыми тестами (docs/AUDIT.md, раздел
«Тесты»): readiness-проба, деградация при недоступном рельефе, ожидание
работы с таймаутом, разбор интервалов действия зон."""

from datetime import datetime, timezone

import pytest

from test_api_plan import _create_task, _upload_environment, _upload_fleet
from test_jobs import slow_queue  # noqa: F401 — фикстура

from uav_planner.services.environment_service import parse_time_windows
from uav_planner.terrain import ElevationLookupError


# ---------- /api/health/ready ----------

class _Redis:
    def __init__(self, error=None):
        self.error = error

    def ping(self):
        if self.error:
            raise self.error
        return True


def test_ready_reports_ok_when_redis_answers(client, monkeypatch):
    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: _Redis())
    resp = client.get("/api/health/ready")
    body = resp.json()
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["database"] in ("ok", "not configured")
    assert resp.status_code == 200 and body["status"] == "ok"


def test_ready_is_degraded_when_redis_is_down(client, monkeypatch):
    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: _Redis(ConnectionError("нет соединения")))
    resp = client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"].startswith("error: ") and "нет соединения" in body["checks"]["redis"]


# ---------- рельеф недоступен ----------

class _BrokenElevation:
    def elevations(self, points_wgs84):
        raise ElevationLookupError("сервис высот не отвечает")


def test_plan_degrades_to_flat_altitude_when_terrain_is_unavailable(client, monkeypatch):
    monkeypatch.setattr(
        "uav_planner.services.terrain_service.default_elevation_provider", lambda **_: _BrokenElevation(),
    )
    env_id = _upload_environment(client)
    _upload_fleet(client)
    resp = client.post("/api/plans", data={"task_id": _create_task(client, env_id)})
    assert resp.status_code == 200, resp.text
    plan = client.get(f"/api/plans/{resp.json()['id']}").json()

    assert any("рельеф недоступен (сервис высот не отвечает)" in w for w in plan["warnings"])
    assert any("Рельеф недоступен" in line for line in plan["calculation_log"])
    # Маршрут по-прежнему 3D: Z — плоская целевая высота над нулевым рельефом.
    z_values = {round(c[2], 6) for s in plan["sorties"] for c in s["track_geojson"]["coordinates"]}
    assert z_values == {round(plan["height_m"], 6)}


# ---------- ожидание работы ----------

def test_plan_request_waits_up_to_wait_s_then_returns_202(client, slow_queue):  # noqa: F811
    env_id = _upload_environment(client)
    _upload_fleet(client)
    task_id = _create_task(client, env_id)
    slow_queue.enable()
    started = datetime.now(timezone.utc)
    resp = client.post("/api/plans", data={"task_id": task_id, "wait_s": "0.2"})
    waited_s = (datetime.now(timezone.utc) - started).total_seconds()
    assert resp.status_code == 202, resp.text
    assert waited_s >= 0.2  # опрос шел до конца отведенного времени


# ---------- интервалы действия зон ----------

def test_parse_time_windows_normalizes_naive_time_to_utc():
    windows = parse_time_windows([
        {"start": "2026-06-15T05:00:00", "end": "2026-06-15T13:00:00+03:00"},
    ])
    assert windows[0].start == datetime(2026, 6, 15, 5, 0, tzinfo=timezone.utc)
    assert windows[0].end == datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    assert parse_time_windows(None) is None and parse_time_windows([]) is None


def test_parse_time_windows_rejects_reversed_interval():
    with pytest.raises(ValueError):
        parse_time_windows([{"start": "2026-06-15T13:00:00", "end": "2026-06-15T05:00:00"}])
