import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from uav_planner.coverage import boustrophedon_cells


def test_simple_rectangle_is_single_cell():
    rect = box(0, 0, 200, 50)
    cells = boustrophedon_cells(rect)

    assert len(cells) == 1
    assert cells[0].polygon.area == pytest.approx(rect.area, rel=1e-6)
    assert len(cells[0].polygon.interiors) == 0


def test_rectangle_with_hole_has_no_holed_cells_and_preserves_area():
    outer = box(0, 0, 200, 100)
    hole = box(80, 30, 120, 70)
    area = outer.difference(hole)

    cells = boustrophedon_cells(area)

    assert len(cells) >= 2
    for cell in cells:
        assert len(cell.polygon.interiors) == 0
        assert cell.polygon.intersection(hole).area == pytest.approx(0.0, abs=1e-6)

    total_area = sum(c.polygon.area for c in cells)
    assert total_area == pytest.approx(area.area, rel=1e-6)


def test_cells_do_not_overlap():
    outer = box(0, 0, 200, 100)
    hole = box(80, 30, 120, 70)
    area = outer.difference(hole)
    cells = boustrophedon_cells(area)

    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            overlap = cells[i].polygon.intersection(cells[j].polygon).area
            assert overlap == pytest.approx(0.0, abs=1e-6)


def test_multipolygon_input_covers_all_parts():
    part_a = box(0, 0, 50, 50)
    part_b = box(200, 200, 260, 240)
    area = MultiPolygon([part_a, part_b])

    cells = boustrophedon_cells(area)

    total_area = sum(c.polygon.area for c in cells)
    assert total_area == pytest.approx(area.area, rel=1e-6)
    # Обе непересекающиеся части должны быть покрыты хотя бы одной ячейкой каждая.
    assert any(c.polygon.intersects(part_a) for c in cells)
    assert any(c.polygon.intersects(part_b) for c in cells)


def test_empty_area_returns_no_cells():
    assert boustrophedon_cells(Polygon()) == []


def test_min_cell_area_filters_tiny_slivers():
    outer = box(0, 0, 200, 100)
    hole = box(80, 30, 120, 70)
    area = outer.difference(hole)

    cells_default = boustrophedon_cells(area, min_cell_area_m2=1.0)
    cells_strict = boustrophedon_cells(area, min_cell_area_m2=1e6)

    assert len(cells_strict) <= len(cells_default)
