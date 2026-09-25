import math

import pytest

from uav_planner.terrain import ConstantElevationProvider, ElevationLookupError, plan_altitude_profile
from uav_planner.terrain.elevation import OpenTopoDataProvider


class _FakeElevationProvider:
    """Заданный вручную профиль рельефа — детерминированно, без сети."""

    def __init__(self, ground_by_point: dict[tuple[float, float], float]) -> None:
        self._ground_by_point = ground_by_point

    def elevations(self, points_wgs84):
        return [self._ground_by_point[p] for p in points_wgs84]


def test_flat_terrain_holds_constant_agl():
    waypoints = [(37.0, 55.0), (37.01, 55.0), (37.02, 55.0)]
    provider = ConstantElevationProvider(height_m=150.0)

    z = plan_altitude_profile(waypoints, [1000.0, 1000.0], target_agl_m=100.0, climb_angle_deg=15.0, provider=provider)

    assert z == pytest.approx([250.0, 250.0, 250.0])


def test_gentle_slope_is_followed_exactly_within_climb_angle_limit():
    # Рельеф поднимается на 50 м за 1000 м — это гораздо меньше, чем угол
    # 15° позволяет (max_step = 1000·tan(15°) ≈ 268 м), поэтому высота над
    # поверхностью должна остаться ровно целевой на всех точках.
    waypoints = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]
    ground = {waypoints[0]: 0.0, waypoints[1]: 50.0, waypoints[2]: 100.0}
    provider = _FakeElevationProvider(ground)

    z = plan_altitude_profile(waypoints, [1000.0, 1000.0], target_agl_m=100.0, climb_angle_deg=15.0, provider=provider)

    assert z == pytest.approx([100.0, 150.0, 200.0])


def test_sharp_cliff_is_capped_by_climb_angle_not_followed_instantly():
    # Резкий обрыв рельефа (+500 м на 100 м пути) физически невозможно
    # догнать по вертикали за один шаг — высота растёт не быстрее, чем
    # позволяет угол набора.
    waypoints = [(0.0, 0.0), (0.001, 0.0)]
    ground = {waypoints[0]: 0.0, waypoints[1]: 500.0}
    provider = _FakeElevationProvider(ground)
    climb_angle_deg = 15.0
    distance_m = 100.0

    z = plan_altitude_profile(
        waypoints, [distance_m], target_agl_m=100.0, climb_angle_deg=climb_angle_deg, provider=provider
    )

    max_step = distance_m * math.tan(math.radians(climb_angle_deg))
    assert z[0] == pytest.approx(100.0)
    assert z[1] == pytest.approx(z[0] + max_step)
    assert z[1] < ground[waypoints[1]] + 100.0  # цель (600 м) физически не достигнута за один шаг


def test_mismatched_distances_length_raises():
    with pytest.raises(ValueError):
        plan_altitude_profile(
            [(0.0, 0.0), (0.01, 0.0)], distances_m=[], target_agl_m=100.0,
            climb_angle_deg=15.0, provider=ConstantElevationProvider(0.0),
        )


def test_empty_waypoints_returns_empty_profile():
    assert plan_altitude_profile([], [], target_agl_m=100.0, climb_angle_deg=15.0, provider=ConstantElevationProvider(0.0)) == []


def test_constant_elevation_provider_repeats_the_same_value():
    provider = ConstantElevationProvider(height_m=321.0)
    assert provider.elevations([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]) == [321.0, 321.0, 321.0]


# ---------- OpenTopoDataProvider (без сети — HTTP замокан) ----------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_open_topo_data_provider_batches_and_caches(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["locations"])
        n = params["locations"].count("|") + 1
        return _FakeResponse({"results": [{"elevation": 100.0 + i} for i in range(n)]})

    monkeypatch.setattr("uav_planner.terrain.elevation.requests.get", fake_get)
    monkeypatch.setattr("uav_planner.terrain.elevation.time.sleep", lambda s: None)

    provider = OpenTopoDataProvider("https://example.invalid/v1/srtm90m")
    points = [(37.0, 55.0), (37.1, 55.1)]

    first = provider.elevations(points)
    assert first == [100.0, 101.0]
    assert len(calls) == 1

    # Повторный запрос тех же точек — из кэша, без нового HTTP-вызова.
    second = provider.elevations(points)
    assert second == first
    assert len(calls) == 1


def test_open_topo_data_provider_raises_lookup_error_on_network_failure(monkeypatch):
    import requests

    def fake_get(url, params, timeout):
        raise requests.exceptions.ConnectionError("no network in this sandbox")

    monkeypatch.setattr("uav_planner.terrain.elevation.requests.get", fake_get)

    provider = OpenTopoDataProvider("https://example.invalid/v1/srtm90m")
    with pytest.raises(ElevationLookupError):
        provider.elevations([(37.0, 55.0)])
