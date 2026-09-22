"""Исключения предметной области.

Вынесены из модулей API, чтобы репозитории, сервисы и роутеры говорили на одном
языке ошибок и не импортировали друг друга ради одного класса. Слой HTTP
переводит их в коды ответов, сама логика о HTTP не знает.
"""

from __future__ import annotations


class GalsError(Exception):
    """Базовая ошибка сервиса."""


class NotFoundError(GalsError, KeyError):
    """Запрошенной записи нет в хранилище."""


class ValidationError(GalsError, ValueError):
    """Входные данные не прошли проверку требований модуля."""


class ConflictError(GalsError):
    """Конкурентное изменение: версия или статус в хранилище уже другие."""


class PlanInfeasibleError(GalsError, ValueError):
    """Расчет невозможен целиком (ПЛН.ФТ.10) — причина в тексте."""
