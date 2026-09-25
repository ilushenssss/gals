"""Провайдер высот рельефа для облета (раздел 3.4 статьи «Multiple fixed-wing
UAVs collaborative coverage 3D» и Ответы_экспертов_Геоскан.pdf, вопрос 13).

Высоты запрашиваются только в естественных точках маршрута (концы галсов и
переходов), а не на каждом шаге дискретизации безопасности (20 м) — эксперты
явно предупреждают, что запрос высот к серверу — долгая операция, и
рекомендуют «строить маршрут с шагом». Десятки точек на вылет, не тысячи.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

import requests

log = logging.getLogger(__name__)

_BATCH_SIZE = 100  # лимит Open Topo Data на один запрос
_MIN_REQUEST_INTERVAL_S = 1.0  # лимит 1 запрос/сек
_COORD_ROUND_NDIGITS = 5  # ~1 м — соседние галсы часто делят точку почти точно


class ElevationProvider(Protocol):
    def elevations(self, points_wgs84: list[tuple[float, float]]) -> list[float]:
        """Высота рельефа (м) для точек ``(lon, lat)``, в том же порядке."""
        ...


class ElevationLookupError(RuntimeError):
    """Рельеф недоступен — решение (упасть или деградировать на плоскую
    высоту с предупреждением) принимает вызывающая сторона, не этот модуль."""


class ConstantElevationProvider:
    """Постоянная высота рельефа — для тестов и как честная деградация без
    сети (см. ``services.plan_service``, а не тихая заглушка «рельефа нет»)."""

    def __init__(self, height_m: float = 0.0) -> None:
        self.height_m = height_m

    def elevations(self, points_wgs84: list[tuple[float, float]]) -> list[float]:
        return [self.height_m] * len(points_wgs84)


class OpenTopoDataProvider:
    """Клиент публичного REST Open Topo Data (совместим с Google Elevation
    API) — см. docs/realization/uav_planner_methods.html, раздел 11.

    Кэш по координатам, округленным до ``_COORD_ROUND_NDIGITS`` знаков —
    живёт вместе с экземпляром провайдера (один расчёт плана), не глобально.

    ``on_request`` зовется перед каждым запросом: при лимите 1 запрос/с сотня
    пакетов — это минуты, и фоновая работа через него шлет heartbeat и
    проверяет отмену (см. ``jobs.progress.ProgressReporter.tick``).
    """

    def __init__(
        self, base_url: str, timeout_s: float = 10.0, on_request: Callable[[], None] | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._on_request = on_request
        self._cache: dict[tuple[float, float], float] = {}
        self._last_request_at: float | None = None

    def elevations(self, points_wgs84: list[tuple[float, float]]) -> list[float]:
        keys = [(round(lon, _COORD_ROUND_NDIGITS), round(lat, _COORD_ROUND_NDIGITS)) for lon, lat in points_wgs84]
        missing = [k for k in dict.fromkeys(keys) if k not in self._cache]

        for start in range(0, len(missing), _BATCH_SIZE):
            self._fetch_batch(missing[start : start + _BATCH_SIZE])

        try:
            return [self._cache[k] for k in keys]
        except KeyError as exc:
            raise ElevationLookupError(f"высота рельефа не получена для точки {exc.args[0]}") from exc

    def _fetch_batch(self, keys: list[tuple[float, float]]) -> None:
        if self._on_request is not None:
            self._on_request()
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < _MIN_REQUEST_INTERVAL_S:
                time.sleep(_MIN_REQUEST_INTERVAL_S - elapsed)

        locations = "|".join(f"{lat},{lon}" for lon, lat in keys)
        try:
            resp = requests.get(self._base_url, params={"locations": locations}, timeout=self._timeout_s)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ElevationLookupError(f"Open Topo Data недоступен: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()

        results = payload.get("results", [])
        if len(results) != len(keys):
            raise ElevationLookupError("Open Topo Data вернул неожиданное число результатов")
        for key, result in zip(keys, results):
            elevation = result.get("elevation")
            if elevation is None:
                raise ElevationLookupError(f"Open Topo Data не знает высоту для точки {key}")
            self._cache[key] = float(elevation)
