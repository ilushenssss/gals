"""Сквозная проверка: рабочая область (geometry) -> ячейки -> галсы (coverage)."""

import pytest
from shapely.geometry import Polygon

from uav_planner.geometry import AllowedZone, HeightRange, NoFlyZone, compute_working_area
from uav_planner.coverage import boustrophedon_cells, generate_tracks, split_long_track


def test_full_pipeline_area_to_tracks():
    survey_area = Polygon([(0, 0), (1000, 0), (1000, 600), (0, 600)])
    allowed = [AllowedZone(id="a1", polygon=Polygon([(-50, -50), (1050, -50), (1050, 650), (-50, 650)]), height=HeightRange(0, 300))]
    no_fly = [NoFlyZone(id="nf1", polygon=Polygon([(400, 200), (500, 200), (500, 400), (400, 400)]))]

    working = compute_working_area(survey_area, allowed, no_fly, [], survey_height_m=100.0)
    cells = boustrophedon_cells(working, wind_bearing_deg=10.0)

    assert len(cells) >= 2  # БПЗ обязана разрезать область хотя бы на пару ячеек
    total_cell_area = sum(c.polygon.area for c in cells)
    assert total_cell_area == pytest.approx(working.area, rel=1e-6)

    all_tracks = []
    for cell in cells:
        tracks = generate_tracks(cell, spacing_m=50.0)
        for t in tracks:
            all_tracks.extend(split_long_track(t, max_len_m=300.0))

    assert len(all_tracks) > 0
    for t in all_tracks:
        assert t.length <= 300.0 + 1e-6
        # Ни один галс не заходит в бесполетную зону.
        assert t.intersection(no_fly[0].polygon).length == pytest.approx(0.0, abs=1e-6)
