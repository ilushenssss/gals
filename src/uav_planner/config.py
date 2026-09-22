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

Часть параметров модели пока объявлена, но не подключена к коду — у чистых
математических модулей свои значения по умолчанию, и связать их можно будет
только вместе с соответствующим расширением расчета. Такие настройки вынесены
в отдельную секцию и помечены явно: настройка, которая ничего не меняет, но
выглядит рабочей, хуже ее отсутствия.
"""

from __future__ import annotations

from functools import lru_cache
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
    log_level: str = "INFO"
    # Формат журнала: "json" — по строке JSON на запись (прод, машинный разбор),
    # "text" — читаемая строка (разработка). "auto" выбирает по env.
    log_format: Literal["auto", "json", "text"] = "auto"

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

    # --- расчет ------------------------------------------------------------
    # Сколько секунд POST /api/plans ждет результата, прежде чем отдать 202:
    # в dev/test успевает синхронно (сцены считаются миллисекунды), в проде 0.
    plan_sync_wait_seconds: float = 30.0
    plan_time_limit_s: int = 1800  # ПЛН.ФТ.3
    plan_hard_time_limit_s: int = 1980
    max_auto_recalc: int = 3  # БЕЗ.ФТ.3
    cancel_grace_seconds: int = 30
    # Работа «Выполняется», у которой heartbeat старше этого порога, считается
    # осиротевшей: ее воркер погиб вместе с контейнером. Добивает такие работы
    # периодическая задача janitor'а (сервис `beat`), а не чтение.
    job_stale_after_seconds: int = 120
    job_sweep_interval_seconds: int = 30

    # --- параметры математической модели ------------------------------------
    energy_reserve: float = 0.20  # η, БЕЗ: остаток энергии не ниже 20 %
    maneuver_margin: float = 0.0  # m_ман

    # --- объявлено требованиями, но еще не подключено -------------------------
    # Значения ниже сейчас ни на что не влияют: проверки безопасности берут
    # собственные значения по умолчанию из uav_planner.safety.checks, а лимит
    # на размер загружаемого файла не проверяется нигде. Оставлены, чтобы имя
    # параметра из требований уже существовало, но подключать их надо вместе с
    # тем кодом, который начнет их читать.
    coverage_tolerance: float = 0.01  # допуск непокрытой площади
    separation_distance_m: float = 100.0  # D_min
    separation_time_s: float = 30.0  # ε_t
    separation_height_m: float = 20.0  # ε_h
    discretize_step_s: float = 5.0  # шаг дискретизации траектории для проверок
    max_upload_bytes: int = 50 * 1024 * 1024

    @property
    def log_as_json(self) -> bool:
        return self.log_format == "json" or (self.log_format == "auto" and self.env != "dev")

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
