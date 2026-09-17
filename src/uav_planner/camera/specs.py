"""Справочник камер и высотных диапазонов трех моделей БВС Геоскан.

Источник состава парка и совместимой нагрузки — концепция решения, раздел 3
(«Парк БВС»): Геоскан 201 несет RGB (Sony RX1RM2/RM3, Riebo R4/R6, ZV-E10) и
мультиспектральную Pollux; Геоскан Gemini — RGB PF1B и Pollux; Геоскан 801 —
тепловизор 640×512 и RGB-камеру 12 Мп.

Важно: для RX1RM2 и PF1B фокусное расстояние и размер матрицы — паспортные
значения производителя, расчет по ним воспроизводит контрольную таблицу
концепции (раздел 3) с точностью несколько процентов. Для Pollux, тепловизора
640×512 и 12-мегапиксельной камеры 801 точных паспортов на момент реализации
нет — приведены правдоподобные оценки по типовым сенсорам такого класса;
это зафиксированное допущение (см. «Открытые неточности» в плане реализации),
подлежит уточнению по паспортам Геоскан.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CameraSpec:
    """Параметры камеры, нужные для расчета GSD/высоты/полосы захвата.

    ``focal_length_mm`` и ``sensor_width_mm``/``sensor_height_mm`` — в одних и тех же
    единицах (мм), поэтому конкретная единица не важна, важно их отношение.
    """

    name: str
    spectrum: str  # "rgb" | "multispectral" | "thermal"
    focal_length_mm: float
    sensor_width_mm: float
    sensor_height_mm: float
    frame_width_px: int
    frame_height_px: int
    verified: bool = True  # False — оценка, не паспортное значение (см. модуль docstring)


CAMERA_SPECS: dict[str, CameraSpec] = {
    "rx1rm2": CameraSpec(
        name="Sony RX1RM2",
        spectrum="rgb",
        focal_length_mm=35.0,
        sensor_width_mm=35.9,
        sensor_height_mm=24.0,
        frame_width_px=7952,
        frame_height_px=5304,
    ),
    "pf1b": CameraSpec(
        name="PF1B",
        spectrum="rgb",
        focal_length_mm=20.0,
        sensor_width_mm=23.5,
        sensor_height_mm=15.6,
        frame_width_px=6000,
        frame_height_px=4000,
    ),
    "pollux": CameraSpec(
        name="Pollux",
        spectrum="multispectral",
        focal_length_mm=8.0,
        sensor_width_mm=4.8,
        sensor_height_mm=3.6,
        frame_width_px=1280,
        frame_height_px=960,
        verified=False,
    ),
    "thermal640": CameraSpec(
        name="Тепловизор 640x512",
        spectrum="thermal",
        focal_length_mm=13.0,
        sensor_width_mm=7.68,   # 640 px * 12 мкм — типовой шаг пикселя неохлаждаемого болометра
        sensor_height_mm=6.14,  # 512 px * 12 мкм
        frame_width_px=640,
        frame_height_px=512,
        verified=False,
    ),
    "cam12mp": CameraSpec(
        name="RGB 12 Мп (Геоскан 801)",
        spectrum="rgb",
        focal_length_mm=4.3,
        sensor_width_mm=6.17,   # типовая матрица 1/2.3"
        sensor_height_mm=4.63,
        frame_width_px=4000,
        frame_height_px=3000,
        verified=False,
    ),
}


@dataclass(frozen=True)
class UavHeightLimits:
    """Высотный диапазон модели БВС над поверхностью, метры.

    ``h_max is None`` — паспортный потолок не указан явно (только нижняя граница),
    как у Геоскан 201 («не ниже 100 м»); верхнюю границу в этом случае задают
    ограничения воздушного пространства, а не сама модель.
    """

    h_min: float
    h_max: Optional[float] = None

    def __post_init__(self) -> None:
        if self.h_max is not None and self.h_min > self.h_max:
            raise ValueError(f"h_min ({self.h_min}) больше h_max ({self.h_max})")

    def contains(self, h: float) -> bool:
        if h < self.h_min:
            return False
        return self.h_max is None or h <= self.h_max


@dataclass(frozen=True)
class UavModel:
    name: str
    height_limits: UavHeightLimits
    compatible_cameras: tuple[str, ...]


UAV_MODELS: dict[str, UavModel] = {
    "geoscan-201": UavModel(
        name="Геоскан 201",
        height_limits=UavHeightLimits(h_min=100.0, h_max=None),
        compatible_cameras=("rx1rm2", "pollux"),
    ),
    "geoscan-gemini": UavModel(
        name="Геоскан Gemini",
        height_limits=UavHeightLimits(h_min=0.0, h_max=500.0),
        compatible_cameras=("pf1b", "pollux"),
    ),
    "geoscan-801": UavModel(
        name="Геоскан 801",
        height_limits=UavHeightLimits(h_min=50.0, h_max=500.0),
        compatible_cameras=("thermal640", "cam12mp"),
    ),
}


def is_height_allowed(uav_model_key: str, height_m: float) -> bool:
    """Входит ли высота съемки в высотный диапазон модели БВС."""
    return UAV_MODELS[uav_model_key].height_limits.contains(height_m)


def is_camera_compatible(uav_model_key: str, camera_key: str) -> bool:
    """Совместима ли камера с моделью БВС (несет ли ее модель по спецификации)."""
    return camera_key in UAV_MODELS[uav_model_key].compatible_cameras
