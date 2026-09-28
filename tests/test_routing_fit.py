import pytest
from shapely.geometry import LineString, Point

from uav_planner.routing import Vehicle, cluster_assign_and_route, fit_tracks_to_vehicles
from uav_planner.routing.cluster import Track

LAUNCH = Point(0, 0)


def vehicle(id_="A", speed_mps=10.0, budget_s=10_000.0, launch_point=LAUNCH, comm_range_m=None):
    return Vehicle(id=id_, speed_mps=speed_mps, budget_s=budget_s, launch_point=launch_point, comm_range_m=comm_range_m)


def test_feasible_track_is_left_as_is():
    track = LineString([(0, 100), (1000, 100)])
    result = fit_tracks_to_vehicles([track], [vehicle()])
    assert result.tracks == [track]
    assert result.out_of_comm_range == []


def test_track_longer_than_one_sortie_is_cut_into_equal_feasible_parts():
    # Галс поперек направления на площадку: целиком — 3162 + 6000 + 3162 м,
    # больше бюджета 7000 м; две части — ближний конец 1000 м + 3000 + 3162,
    # все еще больше; три части по 2000 м укладываются каждая.
    track = LineString([(-3000, 1000), (3000, 1000)])
    v = vehicle(budget_s=700.0)
    result = fit_tracks_to_vehicles([track], [v])

    assert len(result.tracks) == 3
    assert [p.length for p in result.tracks] == pytest.approx([2000.0] * 3)
    routed = cluster_assign_and_route([Track(f"t{i}", p) for i, p in enumerate(result.tracks)], [v])
    assert routed.unassigned_tracks == []


def test_unreachable_part_stays_one_piece_and_reachable_part_is_not_shredded():
    # Дальний конец недостижим ни при каком разбиении (туда и обратно больше
    # бюджета) — выполнимая ближняя часть не должна рассыпаться на «пунктир».
    track = LineString([(0, 100), (5000, 100)])
    result = fit_tracks_to_vehicles([track], [vehicle(budget_s=600.0)])

    assert sum(p.length for p in result.tracks) == pytest.approx(track.length)
    assert len(result.tracks) <= 4


def test_part_outside_comm_range_is_reported_not_routed():
    track = LineString([(-5000, 1000), (5000, 1000)])
    v = vehicle(comm_range_m=2000.0)
    result = fit_tracks_to_vehicles([track], [v])

    inside = sum(p.length for p in result.tracks)
    outside = sum(p.length for p in result.out_of_comm_range)
    assert inside + outside == pytest.approx(track.length, abs=1.0)
    assert inside == pytest.approx(2 * (2000**2 - 1000**2) ** 0.5, rel=0.01)
    for piece in result.tracks:
        assert all(LAUNCH.distance(Point(c)) <= 2000.0 + 1e-6 for c in piece.coords)


def test_no_comm_clipping_when_some_vehicle_has_unlimited_range():
    track = LineString([(-5000, 1000), (5000, 1000)])
    result = fit_tracks_to_vehicles([track], [vehicle("A", comm_range_m=2000.0), vehicle("B")])
    assert result.out_of_comm_range == []
    assert sum(p.length for p in result.tracks) == pytest.approx(track.length)
