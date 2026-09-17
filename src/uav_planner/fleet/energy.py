"""Энергобюджет вылета — см. Математическая_модель.md, раздел 9.

T_бюдж = T_max · (1 − η) · (1 − m_ман)
"""

from __future__ import annotations

from .specs import UavModelSpec

DEFAULT_ENERGY_RESERVE = 0.20  # отсечка остатка энергии 20% — требование экспертов


def flight_time_budget_s(
    model: UavModelSpec,
    energy_reserve: float = DEFAULT_ENERGY_RESERVE,
    maneuver_margin: float = 0.0,
) -> float:
    """Бюджет времени полета на один вылет, секунды."""
    if not (0.0 <= energy_reserve < 1.0):
        raise ValueError(f"energy_reserve должен быть в [0, 1), получено {energy_reserve}")
    if not (0.0 <= maneuver_margin < 1.0):
        raise ValueError(f"maneuver_margin должен быть в [0, 1), получено {maneuver_margin}")
    max_s = model.max_flight_time_min * 60.0
    return max_s * (1.0 - energy_reserve) * (1.0 - maneuver_margin)
