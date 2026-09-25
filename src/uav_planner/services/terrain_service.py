"""Провайдер высот рельефа по умолчанию — общая точка входа для
``plan_service`` (встраивает рельеф в маршрут) и ``safety_service``
(независимо перепроверяет высоту над рельефом, см. ``safety.checks.
check_max_altitude``): оба модуля обязаны использовать ОДНУ настройку
включения/выключения и один и тот же адрес провайдера, но каждый создает
свой экземпляр — кэш координат не должен утекать между расчетом и проверкой,
иначе проверка перестанет быть независимой.

Отдельная функция, а не прямой вызов ``OpenTopoDataProvider(...)`` в
модулях-потребителях — так тесты подменяют именно эту точку (см.
tests/conftest.py, фикстура ``no_network_terrain``) и автосюит не ходит в
сеть, хотя в проде рельеф включен по умолчанию.
"""

from __future__ import annotations

from typing import Callable

from uav_planner.config import get_settings
from uav_planner.terrain import ElevationProvider, OpenTopoDataProvider


def default_elevation_provider(on_request: Callable[[], None] | None = None) -> ElevationProvider | None:
    """``on_request`` — хук перед каждым запросом к сервису высот (heartbeat
    и отмена фоновой работы)."""
    settings = get_settings()
    if not settings.terrain_enabled:
        return None
    return OpenTopoDataProvider(settings.terrain_provider_url, on_request=on_request)
