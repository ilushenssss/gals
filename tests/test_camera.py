import pytest

from uav_planner.camera import (
    CAMERA_SPECS,
    CameraError,
    is_height_allowed,
    is_camera_compatible,
    survey_height,
    swath_width,
    footprint_length,
    track_spacing,
    trigger_spacing,
    plan_survey_geometry,
)


# Контрольные значения — таблица концепции решения, раздел 3 («Парк БВС»),
# GSD 3 см, поперечное перекрытие 70%.

def test_rx1rm2_matches_reference_table():
    camera = CAMERA_SPECS["rx1rm2"]
    h = survey_height(camera, gsd_cm=3.0)
    b = swath_width(camera, h)
    d = track_spacing(b, overlap_lateral=0.7)

    assert h == pytest.approx(230.0, rel=0.05)
    assert b == pytest.approx(236.0, rel=0.05)
    assert d == pytest.approx(71.0, rel=0.05)


def test_pf1b_matches_reference_table():
    camera = CAMERA_SPECS["pf1b"]
    h = survey_height(camera, gsd_cm=3.0)
    b = swath_width(camera, h)
    d = track_spacing(b, overlap_lateral=0.7)

    assert h == pytest.approx(155.0, rel=0.05)
    assert b == pytest.approx(180.0, rel=0.05)
    assert d == pytest.approx(54.0, rel=0.05)


def test_pollux_within_order_of_magnitude_of_reference_table():
    # Спецификация Pollux не подтверждена паспортом (см. specs.py) — допуск шире.
    camera = CAMERA_SPECS["pollux"]
    h = survey_height(camera, gsd_cm=3.0)
    b = swath_width(camera, h)
    d = track_spacing(b, overlap_lateral=0.7)

    assert h == pytest.approx(70.0, rel=0.2)
    assert b == pytest.approx(43.0, rel=0.2)
    assert d == pytest.approx(13.0, rel=0.2)


def test_survey_height_scales_linearly_with_gsd():
    camera = CAMERA_SPECS["rx1rm2"]
    h1 = survey_height(camera, gsd_cm=3.0)
    h2 = survey_height(camera, gsd_cm=6.0)
    assert h2 == pytest.approx(2 * h1)


def test_survey_height_rejects_non_positive_gsd():
    camera = CAMERA_SPECS["rx1rm2"]
    with pytest.raises(ValueError):
        survey_height(camera, gsd_cm=0.0)


def test_track_spacing_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        track_spacing(100.0, overlap_lateral=1.0)
    with pytest.raises(ValueError):
        track_spacing(100.0, overlap_lateral=-0.1)


def test_trigger_spacing_matches_footprint_scaling():
    camera = CAMERA_SPECS["pf1b"]
    h = survey_height(camera, gsd_cm=3.0)
    footprint = footprint_length(camera, h)
    b = trigger_spacing(footprint, overlap_forward=0.8)
    assert b == pytest.approx(footprint * 0.2)


def test_is_height_allowed_geoscan_201_no_upper_bound():
    assert is_height_allowed("geoscan-201", 100.0) is True
    assert is_height_allowed("geoscan-201", 5000.0) is True
    assert is_height_allowed("geoscan-201", 99.9) is False


def test_is_height_allowed_geoscan_801_bounded_range():
    assert is_height_allowed("geoscan-801", 50.0) is True
    assert is_height_allowed("geoscan-801", 500.0) is True
    assert is_height_allowed("geoscan-801", 49.9) is False
    assert is_height_allowed("geoscan-801", 500.1) is False


def test_is_camera_compatible():
    assert is_camera_compatible("geoscan-201", "rx1rm2") is True
    assert is_camera_compatible("geoscan-201", "pf1b") is False
    assert is_camera_compatible("geoscan-gemini", "pf1b") is True
    assert is_camera_compatible("geoscan-801", "thermal640") is True


def test_plan_survey_geometry_happy_path():
    # H≈230 м здесь сам по себе выше потолка 150 м (см. следующий тест) —
    # это проверка именно формулы геометрии съемки, поэтому потолок явно
    # снят большим max_altitude_m, а не переопределен GSD.
    geometry = plan_survey_geometry("geoscan-201", "rx1rm2", gsd_cm=3.0, max_altitude_m=1000.0)
    assert geometry.height_m == pytest.approx(230.0, rel=0.05)
    assert geometry.track_spacing_m == pytest.approx(71.0, rel=0.05)


def test_plan_survey_geometry_rejects_height_above_altitude_ceiling():
    # По запросу пользователя: потолок 150 м ограничивается уже здесь, на
    # этапе расчета геометрии съемки по GSD, а не только позже независимой
    # проверкой безопасности (safety.checks.check_max_altitude).
    with pytest.raises(CameraError):
        plan_survey_geometry("geoscan-201", "rx1rm2", gsd_cm=3.0)
    # Более мелкий GSD -> ниже высота -> в пределах потолка, без ошибки.
    geometry = plan_survey_geometry("geoscan-201", "rx1rm2", gsd_cm=1.5)
    assert geometry.height_m <= 150.0


def test_plan_survey_geometry_rejects_incompatible_camera():
    with pytest.raises(CameraError):
        plan_survey_geometry("geoscan-801", "rx1rm2", gsd_cm=3.0)


def test_plan_survey_geometry_rejects_height_out_of_model_range():
    # Мелкий GSD -> низкая высота съемки, ниже минимума 801 (50 м).
    with pytest.raises(CameraError):
        plan_survey_geometry("geoscan-801", "thermal640", gsd_cm=2.0)
