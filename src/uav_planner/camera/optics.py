"""Формулы модели съемки: высота H по целевому GSD, полоса захвата, шаг галсов
и шаг срабатывания камеры. См. «Методы», раздел 4, и «Архитектура кода», модуль
``camera``.

GSD = H * s_w / (f * N_w)  ->  H = GSD * f * N_w / s_w
B = H * s_w / f                                  (ширина полосы захвата)
A = H * s_h / f                                  (длина кадра на земле, вдоль трека)
d = B * (1 - q_попер)                            (шаг между галсами)
b = A * (1 - q_прод)                             (шаг срабатывания камеры)
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import CameraError
from .specs import CameraSpec, is_camera_compatible, is_height_allowed, CAMERA_SPECS

# Потолок высоты полета без специального разрешения (практический предел
# эксплуатации БВС в неклассифицированном пространстве, а не паспортный
# потолок конкретной модели) — единственный источник значения; safety.checks
# импортирует его отсюда для независимой повторной проверки уже готового
# плана (в т.ч. по высоте над рельефом в каждой точке маршрута — см.
# check_max_altitude), а не хранит свою копию. Ограничивается уже здесь, на
# этапе расчета геометрии съемки по GSD (по запросу пользователя): кандидат
# «модель+камера», которому для заданного GSD нужна высота выше потолка,
# отбрасывается как нереализуемый до всего дальнейшего расчета, а не только
# после него.
DEFAULT_MAX_ALTITUDE_M = 150.0


def survey_height(camera: CameraSpec, gsd_cm: float) -> float:
    """Высота съемки H (м над поверхностью) для целевого GSD (см/пиксель)."""
    if gsd_cm <= 0:
        raise ValueError("gsd_cm должен быть положительным")
    gsd_m = gsd_cm / 100.0
    return gsd_m * camera.focal_length_mm * camera.frame_width_px / camera.sensor_width_mm


def swath_width(camera: CameraSpec, height_m: float) -> float:
    """Ширина полосы захвата B (м) на высоте ``height_m``."""
    if height_m <= 0:
        raise ValueError("height_m должен быть положительным")
    return height_m * camera.sensor_width_mm / camera.focal_length_mm


def footprint_length(camera: CameraSpec, height_m: float) -> float:
    """Длина кадра на земле A (м) вдоль трека на высоте ``height_m``."""
    if height_m <= 0:
        raise ValueError("height_m должен быть положительным")
    return height_m * camera.sensor_height_mm / camera.focal_length_mm


def _check_overlap(overlap: float, label: str) -> None:
    if not (0.0 <= overlap < 1.0):
        raise ValueError(f"{label} должно быть в диапазоне [0, 1), получено {overlap}")


def track_spacing(swath_m: float, overlap_lateral: float) -> float:
    """Шаг между соседними галсами d (м) при поперечном перекрытии ``overlap_lateral``."""
    _check_overlap(overlap_lateral, "overlap_lateral")
    return swath_m * (1.0 - overlap_lateral)


def trigger_spacing(footprint_m: float, overlap_forward: float) -> float:
    """Шаг срабатывания камеры b (м) при продольном перекрытии ``overlap_forward``."""
    _check_overlap(overlap_forward, "overlap_forward")
    return footprint_m * (1.0 - overlap_forward)


@dataclass(frozen=True)
class SurveyGeometry:
    """Готовый набор геометрических параметров съемки для связки БВС + камера."""

    uav_model: str
    camera: str
    height_m: float
    swath_m: float
    footprint_m: float
    track_spacing_m: float
    trigger_spacing_m: float


def plan_survey_geometry(
    uav_model_key: str,
    camera_key: str,
    gsd_cm: float,
    overlap_lateral: float = 0.7,
    overlap_forward: float = 0.7,
    max_altitude_m: float = DEFAULT_MAX_ALTITUDE_M,
) -> SurveyGeometry:
    """Собирает геометрию съемки для связки «модель БВС + камера» и целевого GSD.

    Бросает :class:`CameraError`, если камера не совместима с моделью, расчетная
    высота H выходит за высотный диапазон модели (см. «Архитектура кода», раздел 4)
    или выше потолка ``max_altitude_m`` (по умолчанию 150 м — практический предел
    без специального разрешения; независимо от этого предела, ``safety.checks.
    check_max_altitude`` все равно повторно проверяет уже готовый план — там же
    ловится случай, когда высота над рельефом в конкретной точке маршрута
    отклонилась от этой номинальной H из-за угла набора при облете рельефа).
    """
    if not is_camera_compatible(uav_model_key, camera_key):
        raise CameraError(f"камера «{camera_key}» не совместима с моделью «{uav_model_key}»")

    camera = CAMERA_SPECS[camera_key]
    height_m = survey_height(camera, gsd_cm)

    if not is_height_allowed(uav_model_key, height_m):
        raise CameraError(
            f"высота съемки {height_m:.1f} м вне высотного диапазона модели «{uav_model_key}»"
        )
    if height_m > max_altitude_m:
        raise CameraError(
            f"высота съемки {height_m:.1f} м для GSD {gsd_cm:g} см превышает допустимый потолок "
            f"{max_altitude_m:.0f} м — задайте более крупный GSD или другую камеру"
        )

    swath_m = swath_width(camera, height_m)
    footprint_m = footprint_length(camera, height_m)

    return SurveyGeometry(
        uav_model=uav_model_key,
        camera=camera_key,
        height_m=height_m,
        swath_m=swath_m,
        footprint_m=footprint_m,
        track_spacing_m=track_spacing(swath_m, overlap_lateral),
        trigger_spacing_m=trigger_spacing(footprint_m, overlap_forward),
    )
