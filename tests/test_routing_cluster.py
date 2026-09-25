import pytest
from shapely.geometry import LineString, Point

from uav_planner.routing import Track, Vehicle, cluster_assign_and_route

LAUNCH = Point(0, 0)


def track(id_, length_m):
    return Track(id=id_, geometry=LineString([(0, 0), (length_m, 0)]))


def vehicle(id_, speed_mps, budget_s, launch_point=LAUNCH):
    return Vehicle(id=id_, speed_mps=speed_mps, budget_s=budget_s, launch_point=launch_point)


def test_no_vehicles_leaves_everything_unassigned():
    tracks = [track("t1", 100)]
    result = cluster_assign_and_route(tracks, [])
    assert result.sorties_by_vehicle == {}
    assert result.unassigned_tracks == tracks


def test_all_tracks_assigned_when_they_fit():
    tracks = [track(f"t{i}", 100) for i in range(4)]
    vehicles = [vehicle("A", 10.0, 1000.0)]
    result = cluster_assign_and_route(tracks, vehicles)
    assigned = [t for s in result.sorties_by_vehicle["A"] for t in s.tracks]
    assert len(assigned) == 4
    assert result.unassigned_tracks == []


def test_load_balances_across_two_vehicles_at_the_same_launch_point():
    tracks = [track(f"t{i}", 100) for i in range(6)]
    vehicles = [vehicle("A", 10.0, 10_000.0), vehicle("B", 10.0, 10_000.0)]
    result = cluster_assign_and_route(tracks, vehicles)
    load_a = sum(s.flight_time_s for s in result.sorties_by_vehicle["A"])
    load_b = sum(s.flight_time_s for s in result.sorties_by_vehicle["B"])
    assert load_a == pytest.approx(load_b)
    assert sum(len(s.tracks) for s in result.sorties_by_vehicle["A"]) == 3
    assert sum(len(s.tracks) for s in result.sorties_by_vehicle["B"]) == 3


def test_splits_into_multiple_sorties_when_budget_exceeded():
    # 5 галсов по 100м при скорости 10 м/с -> 10с каждый; бюджет 25с -> максимум
    # 2 галса на вылет (20с), третий уже не влезет -> минимум 3 вылета.
    tracks = [track(f"t{i}", 100) for i in range(5)]
    vehicles = [vehicle("A", 10.0, 25.0)]
    result = cluster_assign_and_route(tracks, vehicles)
    sorties = result.sorties_by_vehicle["A"]
    assert len(sorties) >= 3
    for s in sorties:
        assert s.flight_time_s <= 25.0 + 1e-9
    assert sum(len(s.tracks) for s in sorties) == 5
    assert result.unassigned_tracks == []


def test_track_too_long_for_any_vehicle_is_unassigned():
    tracks = [track("huge", 100_000)]
    vehicles = [vehicle("A", 10.0, 100.0)]
    result = cluster_assign_and_route(tracks, vehicles)
    assert result.unassigned_tracks == tracks
    assert all(not s.tracks for s in result.sorties_by_vehicle["A"])


def test_vehicle_rejects_non_positive_speed_or_budget():
    with pytest.raises(ValueError):
        Vehicle(id="A", speed_mps=0.0, budget_s=100.0, launch_point=LAUNCH)
    with pytest.raises(ValueError):
        Vehicle(id="A", speed_mps=10.0, budget_s=0.0, launch_point=LAUNCH)


# ---------- переходы учитываются в бюджете вылета (Split после кластеризации) ----------

def test_transit_to_far_track_counts_against_budget():
    far_track = Track(id="far", geometry=LineString([(500, 0), (650, 0)]))
    vehicles = [vehicle("A", 10.0, 120.0)]
    result = cluster_assign_and_route([far_track], vehicles)
    assert result.unassigned_tracks == [far_track]
    assert result.sorties_by_vehicle["A"] == []


def test_same_track_fits_when_budget_covers_transit():
    far_track = Track(id="far", geometry=LineString([(500, 0), (650, 0)]))
    vehicles = [vehicle("A", 10.0, 200.0)]  # 50+15+65=130с <= 200с
    result = cluster_assign_and_route([far_track], vehicles)
    assert result.unassigned_tracks == []
    sortie = result.sorties_by_vehicle["A"][0]
    assert sortie.tracks == [far_track]
    assert sortie.flight_time_s == pytest.approx(130.0)


def test_second_far_track_forces_new_sortie_because_of_return_transit():
    # Галс A - позади площадки, галс B - далеко впереди: одному вылету оба
    # не по карману (реальный лишний путь в разные стороны), бюджета не
    # хватает -> B должен уйти в новый вылет.
    track_a = Track(id="a", geometry=LineString([(-20, 0), (-10, 0)]))
    track_b = Track(id="b", geometry=LineString([(200, 0), (210, 0)]))
    vehicles = [vehicle("A", 10.0, 45.0)]
    result = cluster_assign_and_route([track_a, track_b], vehicles)
    sorties = result.sorties_by_vehicle["A"]
    assert len(sorties) == 2
    assert result.unassigned_tracks == []
    assert sorties[0].tracks == [track_a] and sorties[0].flight_time_s == pytest.approx(4.0)
    assert sorties[1].tracks == [track_b] and sorties[1].flight_time_s == pytest.approx(42.0)


# ---------- Шаг 2: у разных БВС может быть разная площадка ----------

def test_each_vehicle_uses_its_own_launch_point():
    track_near = Track(id="near", geometry=LineString([(1000, 0), (1010, 0)]))
    track_far = Track(id="far", geometry=LineString([(0, 0), (10, 0)]))
    vehicles = [
        vehicle("A", 10.0, 1000.0, launch_point=Point(1000, 0)),
        vehicle("B", 10.0, 1000.0, launch_point=Point(0, 0)),
    ]
    result = cluster_assign_and_route([track_near, track_far], vehicles)
    a_tracks = {t.id for s in result.sorties_by_vehicle["A"] for t in s.tracks}
    b_tracks = {t.id for s in result.sorties_by_vehicle["B"] for t in s.tracks}
    assert a_tracks == {"near"}
    assert b_tracks == {"far"}
    a_flight = sum(s.flight_time_s for s in result.sorties_by_vehicle["A"])
    b_flight = sum(s.flight_time_s for s in result.sorties_by_vehicle["B"])
    assert a_flight == pytest.approx(2.0)
    assert b_flight == pytest.approx(2.0)


# ---------- Шаг 3: TSP-тур внутри кластера короче наивного порядка ----------

def test_tsp_order_beats_arbitrary_order():
    # Три галса вокруг площадки под разными углами - тур "как дали" (в порядке
    # создания) был бы неоптимален; ближайший сосед + 2-opt должны обойти их
    # компактно (не длиннее суммы отдельных прыжков "туда-обратно к базе").
    t1 = Track(id="1", geometry=LineString([(100, 0), (110, 0)]))
    t2 = Track(id="2", geometry=LineString([(0, 100), (0, 110)]))
    t3 = Track(id="3", geometry=LineString([(100, 100), (110, 100)]))
    vehicles = [vehicle("A", 10.0, 10_000.0, launch_point=Point(0, 0))]
    result = cluster_assign_and_route([t1, t2, t3], vehicles)
    sortie = result.sorties_by_vehicle["A"][0]
    assert {t.id for t in sortie.tracks} == {"1", "2", "3"}
    # Наивный порядок "как в списке" (t1, t2, t3) слетал бы туда-обратно
    # мимо площадки лишний раз; настоящий тур короче.
    naive_order = [t1, t2, t3]

    def naive_cost():
        total, pos = 0.0, Point(0, 0)
        for t in naive_order:
            d_start, d_end = pos.distance(Point(t.geometry.coords[0])), pos.distance(Point(t.geometry.coords[-1]))
            near_first = d_start <= d_end
            total += (d_start if near_first else d_end) + t.length_m
            pos = Point(t.geometry.coords[-1] if near_first else t.geometry.coords[0])
        return total + pos.distance(Point(0, 0))

    assert sortie.flight_time_s * 10.0 <= naive_cost() + 1e-9


# ---------- Шаг 4: балансировка узкого места ----------

def test_bottleneck_balancing_reduces_the_makespan():
    # Двадцать параллельных галсов, и все по близости достаются площадке A
    # (Шаг 2 не смотрит на загрузку): B остается без работы. Шаг 4 обязан
    # переложить на B половину — J1 (время самого загруженного борта)
    # падает почти вдвое.
    tracks = [Track(id=f"n{i}", geometry=LineString([(0, i * 40), (1000, i * 40)])) for i in range(20)]
    vehicles = [
        vehicle("A", 10.0, 10_000.0, launch_point=Point(0, -100)),
        vehicle("B", 10.0, 10_000.0, launch_point=Point(1000, -600)),
    ]

    unbalanced = cluster_assign_and_route(tracks, vehicles, max_balance_iterations=0)
    balanced = cluster_assign_and_route(tracks, vehicles)

    def makespan(result):
        return max(sum(s.flight_time_s for s in sorties) for sorties in result.sorties_by_vehicle.values())

    assert makespan(balanced) < 0.6 * makespan(unbalanced)
    # Балансировка не теряет и не дублирует галсы.
    for result in (unbalanced, balanced):
        ids = [t.id for sorties in result.sorties_by_vehicle.values() for s in sorties for t in s.tracks]
        assert sorted(ids) == sorted(t.id for t in tracks)


def test_balancing_never_trades_a_better_makespan_for_a_smaller_gap():
    # Прежняя балансировка принимала перенос, если он сокращал разрыв между
    # бортами: здесь 252/220 с → 236/260 с — разрыв меньше, но J1 хуже
    # (252 → 260). Критерий «Время» — J1, поэтому такой перенос не делается.
    tracks = [
        Track(id=f"n{i}", geometry=LineString([(i * 200, 0), (i * 200 + (180.0 if i < 6 else 60.0), 0)]))
        for i in range(8)
    ]
    vehicles = [
        vehicle("A", 10.0, 10_000.0, launch_point=Point(0, 0)),
        vehicle("B", 10.0, 10_000.0, launch_point=Point(2500, 0)),
    ]
    unbalanced = cluster_assign_and_route(tracks, vehicles, max_balance_iterations=0)
    balanced = cluster_assign_and_route(tracks, vehicles, balance_epsilon_s=30.0)

    def makespan(result):
        return max(sum(s.flight_time_s for s in sorties) for sorties in result.sorties_by_vehicle.values())

    assert makespan(balanced) <= makespan(unbalanced)


def test_assignment_gives_each_vehicle_a_contiguous_spatial_strip():
    # Воспроизводит реально диагностированную «шахматку»: несколько БВС с
    # разных площадок работают по одной area — соседние по X галсы должны
    # достаться одному и тому же борту, а не расходиться по бортам вперемешку
    # только потому, что на момент обработки конкретного галса баланс
    # нагрузки качнулся в другую сторону.
    tracks = [Track(id=f"n{i}", geometry=LineString([(i * 100, 0), (i * 100, 500)])) for i in range(30)]
    vehicles = [
        vehicle("A", 10.0, 100_000.0, launch_point=Point(-500, 250)),
        vehicle("B", 10.0, 100_000.0, launch_point=Point(1450, -1000)),
        vehicle("C", 10.0, 100_000.0, launch_point=Point(3400, 250)),
    ]

    result = cluster_assign_and_route(tracks, vehicles)
    assert result.unassigned_tracks == []

    track_x = {t.id: t.geometry.coords[0][0] for t in tracks}
    owner: dict[str, str] = {}
    for vid, sorties in result.sorties_by_vehicle.items():
        for sortie in sorties:
            for t in sortie.tracks:
                owner[t.id] = vid

    ordered_owners = [owner[tid] for tid, _ in sorted(track_x.items(), key=lambda kv: kv[1])]
    # Число «пробегов» одного и того же владельца подряд не должно превышать
    # число бортов — иначе кто-то из них появляется, пропадает и появляется
    # снова, перемешавшись с чужими галсами («шахматка»).
    runs = 1 + sum(1 for a, b in zip(ordered_owners, ordered_owners[1:]) if a != b)
    assert runs <= len(vehicles)


def _fixture_case(name):
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))
    tracks = [Track(id=f"g{i}", geometry=LineString([(x0, y0), (x1, y1)])) for i, (x0, y0, x1, y1) in enumerate(data["tracks"])]
    vehicles = [
        vehicle(v["id"], v["speed_mps"], v["budget_s"], launch_point=Point(*v["launch"])) for v in data["vehicles"]
    ]
    return tracks, vehicles


def test_balancing_does_not_leave_the_far_vehicle_idle():
    """Регрессия 25.09.2026 на реальной сцене: все 41 галс ближе к северной
    ВПП. Балансировка сделала один перенос, на втором эвристика тура дала +20 с,
    и цикл остановился: 801-02 получил 40 галсов (два вылета и замена АКБ,
    J1 ≈ 72 мин), 801-01 — один (8 мин). Кроме того, загрузка оценивалась
    длиной тура, а не вылетами: 1927 с при бюджете 1920 — лишний вылет."""
    overhead_s = 15 * 60.0  # замена АКБ, как в расписании

    tracks, vehicles = _fixture_case("routing_moscow_two_sites.json")
    result = cluster_assign_and_route(tracks, vehicles)

    assert result.unassigned_tracks == []
    counts = {vid: sum(len(s.tracks) for s in sorties) for vid, sorties in result.sorties_by_vehicle.items()}
    assert sum(counts.values()) == 41
    assert min(counts.values()) >= 10, counts

    # J1 без расписания: налет вылетов борта плюс замена АКБ между ними.
    def busy_s(sorties):
        return sum(s.flight_time_s for s in sorties) + overhead_s * (len(sorties) - 1)

    j1 = max(busy_s(s) for s in result.sorties_by_vehicle.values())
    assert j1 <= 55 * 60, j1  # было ≈ 4300 с
    # Весь объем в два вылета не помещается, но лишних быть не должно.
    assert sum(len(s) for s in result.sorties_by_vehicle.values()) == 3
