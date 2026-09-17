import pytest
from shapely import affinity
from shapely.geometry import LineString, box

from uav_planner.coverage import Cell, CoverageError, generate_tracks, split_long_track


def test_generate_tracks_count_and_length_for_axis_aligned_rectangle():
    cell = Cell(polygon=box(0, 0, 100, 40), direction_deg=0.0)
    tracks = generate_tracks(cell, spacing_m=10.0)

    assert len(tracks) == 4  # 40 / 10
    for t in tracks:
        assert t.length == pytest.approx(100.0, rel=1e-6)


def test_generate_tracks_stay_within_polygon():
    polygon = box(0, 0, 100, 40)
    cell = Cell(polygon=polygon, direction_deg=0.0)
    tracks = generate_tracks(cell, spacing_m=7.0)

    envelope = polygon.buffer(1e-6)
    for t in tracks:
        assert envelope.contains(t)


def test_generate_tracks_respect_rotated_direction():
    base = box(0, 0, 100, 40)
    rotated_polygon = affinity.rotate(base, 25.0, origin=(0, 0))
    cell = Cell(polygon=rotated_polygon, direction_deg=25.0)

    tracks = generate_tracks(cell, spacing_m=10.0)

    assert len(tracks) == 4
    envelope = rotated_polygon.buffer(1e-6)
    for t in tracks:
        assert envelope.contains(t)
        # Курс галса должен быть параллелен направлению ячейки (25° или 205°).
        (x0, y0), (x1, y1) = t.coords[0], t.coords[-1]
        import math

        bearing = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
        assert bearing == pytest.approx(25.0, abs=1e-3)


def test_generate_tracks_splits_around_hole_within_cell():
    # L-образная (невыпуклая) ячейка: при направлении 0 средний галс должен
    # прерываться и возвращаться как несколько отрезков, а не один целый.
    l_shape = box(0, 0, 100, 20).union(box(0, 0, 20, 60))
    cell = Cell(polygon=l_shape, direction_deg=0.0)

    tracks = generate_tracks(cell, spacing_m=10.0)
    lengths = sorted(t.length for t in tracks)

    # Верхние галсы (y > 20) покрывают только узкую полку 0..20, а не всю ширину.
    assert any(length == pytest.approx(20.0, rel=1e-6) for length in lengths)
    assert any(length == pytest.approx(100.0, rel=1e-6) for length in lengths)


def test_generate_tracks_returns_multiple_segments_for_disconnected_cross_section():
    # "U"-образная ячейка: горизонтальный галс выше перемычки пересекает фигуру
    # в двух несвязных местах — должно вернуться два отрезка, а не один.
    left = box(0, 0, 10, 60)
    right = box(40, 0, 50, 60)
    bottom = box(0, 0, 50, 10)
    u_shape = left.union(right).union(bottom)
    cell = Cell(polygon=u_shape, direction_deg=0.0)

    tracks = generate_tracks(cell, spacing_m=60.0)  # единственный галс ровно на y=30

    assert len(tracks) == 2
    for t in tracks:
        assert t.length == pytest.approx(10.0, rel=1e-6)


def test_generate_tracks_rejects_non_positive_spacing():
    cell = Cell(polygon=box(0, 0, 10, 10), direction_deg=0.0)
    with pytest.raises(CoverageError):
        generate_tracks(cell, spacing_m=0.0)


def test_split_long_track_preserves_total_length():
    track = LineString([(0, 0), (250, 0)])
    parts = split_long_track(track, max_len_m=100.0)

    assert len(parts) == 3
    assert sum(p.length for p in parts) == pytest.approx(track.length, rel=1e-9)
    for p in parts:
        assert p.length <= 100.0 + 1e-9


def test_split_long_track_returns_unchanged_when_short_enough():
    track = LineString([(0, 0), (50, 0)])
    parts = split_long_track(track, max_len_m=100.0)
    assert len(parts) == 1
    assert parts[0].equals(track)


def test_split_long_track_rejects_non_positive_max_len():
    track = LineString([(0, 0), (50, 0)])
    with pytest.raises(CoverageError):
        split_long_track(track, max_len_m=0.0)
