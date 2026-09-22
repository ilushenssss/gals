"""Окружение Alembic.

Два момента, специфичных для этого проекта:

1. DSN берется из настроек (``GALS_DATABASE_URL``), а не из alembic.ini — чтобы
   контейнер ``migrate`` и приложение читали одну и ту же переменную.
2. Служебные таблицы PostGIS (``spatial_ref_sys``, ``geometry_columns``,
   ``raster_*``) и автоматически создаваемые GeoAlchemy2 пространственные
   индексы исключаются из autogenerate. Без этого каждая автогенерация
   предлагает дропнуть половину PostGIS.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from uav_planner.config import get_settings
from uav_planner.db.base import Base
import uav_planner.models  # noqa: F401  — регистрирует модели в Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
if settings.database_url:
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata

# Образ postgis/postgis включает расширения postgis_topology и
# postgis_tiger_geocoder, которые создают десятки собственных таблиц. Ни одна
# из них не наша, поэтому autogenerate не должен предлагать их удалить.
def _own_table(name: str | None) -> bool:
    return name in target_metadata.tables


def include_object(obj, name, type_, reflected, compare_to):
    """Autogenerate работает только с таблицами, объявленными в наших моделях."""
    if type_ == "table":
        # Отраженная из БД таблица, которой нет в моделях, — чужая.
        return _own_table(name) if reflected else True
    # Индексы, ограничения и колонки судим по таблице, которой они принадлежат.
    table = getattr(obj, "table", None)
    if table is not None and reflected and not _own_table(table.name):
        return False
    # GeoAlchemy2 создает spatial-индексы сама, вне метаданных Alembic.
    if type_ == "index" and name and name.startswith("idx_") and name.endswith(("_geom", "_the_geom")):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
