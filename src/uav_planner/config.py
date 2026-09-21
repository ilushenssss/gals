"""Настройки сервиса — единственный источник конфигурации (pydantic-settings).

Все значения читаются из переменных окружения с префиксом ``GALS_`` либо из
файла ``.env`` (см. ``.env.example``). Плоская модель вместо вложенной: два
десятка настроек не окупают вложенность, зато имена один-в-один ложатся в
``environment:`` docker compose.

Числовые параметры расчета (лимит времени, резерв энергии, допуски проверок)
вынесены сюда, потому что требования называют их «параметром конфигурации»:
ПЛН.ФТ.3 (лимит 1800 с), БЕЗ.ФТ.3 (не более трех автопересчетов),
Математическая_модель.md (η = 0,20, допуск покрытия, D_min, ε_t, ε_h, шаг
дискретизации траектории).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GALS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- окружение ---------------------------------------------------------
    env: Literal["dev", "test", "prod"] = "dev"
    debug: bool = False
    log_level: str = "INFO"

    # --- хранилище ---------------------------------------------------------
    # Пустая строка = БД не сконфигурирована; на шаге 1 сервис еще работает
    # на хранилищах в памяти, поэтому отсутствие DSN не является ошибкой.
    database_url: str = ""
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_echo_sql: bool = False

    # --- очередь -----------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = ""  # пусто -> redis_url
    celery_queue: str = "gals"
    # Выполнять фоновые задачи прямо в вызывающем потоке, без брокера. Нужен
    # тестам (см. tests/conftest.py) и отладке без Redis; в compose всегда
    # false, иначе получасовой расчет исполнится внутри HTTP-запроса.
    celery_task_always_eager: bool = False

    # --- HTTP --------------------------------------------------------------
    # Фронтенд и API ходят через один origin (nginx в контейнере web либо
    # dev-proxy Vite), поэтому по умолчанию CORS не нужен и список пуст.
    cors_origins: list[str] = []
    max_upload_bytes: int = 50 * 1024 * 1024

    # --- расчет ------------------------------------------------------------
    # Сколько секунд POST /api/plans ждет результата, прежде чем отдать 202:
    # в dev/test успевает синхронно (сцены считаются миллисекунды), в проде 0.
    plan_sync_wait_seconds: float = 30.0
    plan_time_limit_s: int = 1800  # ПЛН.ФТ.3
    plan_hard_time_limit_s: int = 1980
    max_auto_recalc: int = 3  # БЕЗ.ФТ.3
    cancel_grace_seconds: int = 30
    job_heartbeat_seconds: int = 15
    job_stale_after_seconds: int = 120

    # --- параметры математической модели ------------------------------------
    energy_reserve: float = 0.20  # η, БЕЗ: остаток энергии не ниже 20 %
    maneuver_margin: float = 0.0  # m_ман
    coverage_tolerance: float = 0.01  # допуск непокрытой площади
    separation_distance_m: float = 100.0  # D_min
    separation_time_s: float = 30.0  # ε_t
    separation_height_m: float = 20.0  # ε_h
    discretize_step_s: float = 5.0  # шаг дискретизации траектории для проверок

    # --- экспорт -----------------------------------------------------------
    export_dir: Path = Path("/data/exports")
    export_retention_days: int = 30

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
