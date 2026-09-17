import pytest
from shapely.geometry import LineString

from uav_planner.routing import Track, Vehicle, greedy_assign_and_split


def track(id_, length_m):
    return Track(id=id_, geometry=LineString([(0, 0), (length_m, 0)]))


def test_no_vehicles_leaves_everything_unassigned():
    tracks = [track("t1", 100)]
    result = greedy_assign_and_split(tracks, [])
    assert result.sorties_by_vehicle == {}
    assert result.unassigned_tracks == tracks


def test_all_tracks_assigned_when_they_fit():
    tracks = [track(f"t{i}", 100) for i in range(4)]
    vehicles = [Vehicle(id="A", speed_mps=10.0, budget_s=1000.0)]
    result = greedy_assign_and_split(tracks, vehicles)
    assigned = [t for s in result.sorties_by_vehicle["A"] for t in s.tracks]
    assert len(assigned) == 4
    assert result.unassigned_tracks == []


def test_load_balances_across_two_vehicles():
    tracks = [track(f"t{i}", 100) for i in range(6)]
    vehicles = [
        Vehicle(id="A", speed_mps=10.0, budget_s=10_000.0),
        Vehicle(id="B", speed_mps=10.0, budget_s=10_000.0),
    ]
    result = greedy_assign_and_split(tracks, vehicles)
    load_a = sum(s.flight_time_s for s in result.sorties_by_vehicle["A"])
    load_b = sum(s.flight_time_s for s in result.sorties_by_vehicle["B"])
    assert load_a == pytest.approx(load_b)


def test_splits_into_multiple_sorties_when_budget_exceeded():
    # 5 галсов по 100м при скорости 10 м/с -> 10с каждый; бюджет 25с -> максимум
    # 2 галса на вылет (20с), третий уже не влезет -> минимум 3 вылета.
    tracks = [track(f"t{i}", 100) for i in range(5)]
    vehicles = [Vehicle(id="A", speed_mps=10.0, budget_s=25.0)]
    result = greedy_assign_and_split(tracks, vehicles)
    sorties = result.sorties_by_vehicle["A"]
    assert len(sorties) >= 3
    for s in sorties:
        assert s.flight_time_s <= 25.0 + 1e-9
    assert sum(len(s.tracks) for s in sorties) == 5


def test_track_too_long_for_any_vehicle_is_unassigned():
    tracks = [track("huge", 100_000)]
    vehicles = [Vehicle(id="A", speed_mps=10.0, budget_s=100.0)]
    result = greedy_assign_and_split(tracks, vehicles)
    assert result.unassigned_tracks == tracks
    assert all(not s.tracks for s in result.sorties_by_vehicle["A"])


def test_vehicle_rejects_non_positive_speed_or_budget():
    with pytest.raises(ValueError):
        Vehicle(id="A", speed_mps=0.0, budget_s=100.0)
    with pytest.raises(ValueError):
        Vehicle(id="A", speed_mps=10.0, budget_s=0.0)


def test_longer_tracks_prioritized_first_lpt():
    tracks = [track("short", 10), track("long", 900)]
    vehicles = [
        Vehicle(id="A", speed_mps=10.0, budget_s=1000.0),
        Vehicle(id="B", speed_mps=10.0, budget_s=1000.0),
    ]
    result = greedy_assign_and_split(tracks, vehicles)
    # Длинный галс уходит первому (наименее загруженному на тот момент) БВС "A".
    assert result.sorties_by_vehicle["A"][0].tracks[0].id == "long"
    assert result.sorties_by_vehicle["B"][0].tracks[0].id == "short"
