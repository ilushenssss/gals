import pytest
from shapely.geometry import Point, box
from shapely.ops import unary_union

from uav_planner.routing import FleetShare, split_area_between_groups

AREA = box(0, 0, 4000, 2000)


def share(id_, launch, area=AREA, spacing_m=30.0, speed_mps=15.0, vehicles=1, budget_s=3600.0):
    return FleetShare(
        id=id_, area=area, launch_points=(launch,), spacing_m=spacing_m, speed_mps=speed_mps,
        vehicles=vehicles, budget_s=budget_s, overhead_s=900.0,
    )


def test_single_group_gets_the_whole_area():
    parts = split_area_between_groups(AREA, [share("a", Point(0, 0))])
    assert parts["a"].area == pytest.approx(AREA.area)


def test_equal_groups_at_opposite_ends_split_the_area_in_halves_near_their_sites():
    parts = split_area_between_groups(AREA, [share("west", Point(0, 1000)), share("east", Point(4000, 1000))])

    assert parts["west"].area == pytest.approx(AREA.area / 2, rel=0.1)
    assert unary_union(list(parts.values())).area == pytest.approx(AREA.area)
    assert parts["west"].intersection(parts["east"]).area == pytest.approx(0.0, abs=1.0)
    assert parts["west"].centroid.x < parts["east"].centroid.x


def test_more_productive_group_gets_a_larger_share():
    # Втрое больше бортов при той же модели — втрое быстрее снимает.
    parts = split_area_between_groups(
        AREA, [share("big", Point(0, 1000), vehicles=3), share("small", Point(4000, 1000))]
    )
    assert parts["big"].area > 2 * parts["small"].area


def test_chunk_outside_a_group_working_area_goes_to_the_other_group():
    # На высоте группы «low» верхняя половина закрыта (препятствие выше ее
    # высоты съемки) — ей там ничего не достается, хотя по нагрузке она
    # забрала бы и оттуда.
    bottom = box(0, 0, 4000, 1000)
    parts = split_area_between_groups(
        AREA, [share("low", Point(2000, 0), area=bottom, vehicles=3), share("high", Point(2000, 2000))]
    )
    assert parts["low"].area > 0
    assert parts["low"].difference(bottom).area == pytest.approx(0.0, abs=1.0)
    assert unary_union(list(parts.values())).area == pytest.approx(AREA.area)
