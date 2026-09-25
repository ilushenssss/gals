"""Высотный профиль маршрута над рельефом — упрощение алгоритма высотного
планирования (раздел 3.4 статьи «Multiple fixed-wing UAVs collaborative
coverage 3D») для разреженных путевых точек маршрута, а не для сетки ЦММ:

- целевая Z в каждой точке = высота рельефа + целевая высота над
  поверхностью (см. Ответы_экспертов_Геоскан.pdf, вопрос 13 — высота задаётся
  именно над поверхностью, не абсолютная);
- фактический шаг Z между соседними точками ограничен углом набора/снижения
  (аналог формулы 36 статьи), иначе резкий обрыв рельефа дал бы физически
  невыполнимый скачок высоты;
- результат сглаживается скользящей медианой по 3 точкам — простая замена
  полноценного градиентного спуска (GDA, формулы 37-38 статьи), оправданная
  тем, что точек здесь десятки (концы галсов/переходов), а не тысячи узлов
  регулярной сетки.
"""

from __future__ import annotations

import math

from .elevation import ElevationProvider


def plan_altitude_profile(
    waypoints_wgs84: list[tuple[float, float]],
    distances_m: list[float],
    target_agl_m: float,
    climb_angle_deg: float,
    provider: ElevationProvider,
) -> list[float]:
    """Абсолютная высота Z (м) на каждой путевой точке маршрута.

    ``distances_m[i]`` — расстояние по земле между ``waypoints_wgs84[i]`` и
    ``waypoints_wgs84[i + 1]``: пересчитывать его из lon/lat не нужно, оно уже
    посчитано маршрутизацией/обходом препятствий на вызывающей стороне.
    """
    if not waypoints_wgs84:
        return []
    if len(waypoints_wgs84) != len(distances_m) + 1:
        raise ValueError("distances_m должен содержать на одно значение меньше, чем waypoints_wgs84")

    ground_m = provider.elevations(waypoints_wgs84)
    desired_z = [g + target_agl_m for g in ground_m]

    actual_z = [desired_z[0]]
    for i in range(1, len(desired_z)):
        max_step = distances_m[i - 1] * math.tan(math.radians(climb_angle_deg))
        prev = actual_z[-1]
        actual_z.append(min(max(desired_z[i], prev - max_step), prev + max_step))

    return _smooth_moving_median(actual_z)


def _smooth_moving_median(values: list[float]) -> list[float]:
    if len(values) < 3:
        return list(values)
    smoothed = [values[0]]
    for i in range(1, len(values) - 1):
        smoothed.append(sorted(values[i - 1 : i + 2])[1])
    smoothed.append(values[-1])
    return smoothed
