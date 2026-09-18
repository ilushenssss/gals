"""Общий словарь предметной области: ошибки и перечисления."""

from .errors import (
    ConflictError,
    GalsError,
    NotFoundError,
    PlanInfeasibleError,
    ValidationError,
)

__all__ = [
    "ConflictError", "GalsError", "NotFoundError", "PlanInfeasibleError",
    "ValidationError",
]
