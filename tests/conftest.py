"""Общие фикстуры тестов.

Хранилище — настоящий PostgreSQL с PostGIS: SQLite не подходит, потому что в
схеме есть geometry-колонки и частичные индексы. База берется так:

1. ``GALS_TEST_DATABASE_URL`` — если задана (CI, сервис ``db`` из compose);
2. иначе поднимается контейнер через testcontainers;
3. если ни того, ни другого нет — тесты с пометкой ``db`` пропускаются, а
   тесты чистой математики (их большинство) идут как обычно.

Изоляция теста — вложенная транзакция: соединение открывает внешнюю
транзакцию, сессия работает внутри нее в режиме ``create_savepoint``, поэтому
даже реальные ``commit()`` внутри приложения откатываются в конце теста.
Сессия кладется в контекст заранее, и middleware приложения видит, что сессия
уже есть, и не открывает свою.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from uav_planner.config import get_settings
from uav_planner.db import session as db_session


def _testcontainer_url():
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        return None, None
    container = PostgresContainer("postgis/postgis:16-3.4", driver="psycopg")
    try:
        container.start()
    except Exception:
        return None, None
    return container.get_connection_url(), container


@pytest.fixture(scope="session")
def database_url():
    url = os.getenv("GALS_TEST_DATABASE_URL")
    if url:
        # Тесты изолируются откатом транзакции, но откат не уберет данные,
        # которые положило туда работающее приложение. Прогон по рабочей базе
        # дает загадочные падения в тестах вида «парк еще не загружен», поэтому
        # это запрещено явно.
        app_url = os.getenv("GALS_DATABASE_URL") or get_settings().database_url
        if app_url and app_url == url:
            pytest.fail(
                "GALS_TEST_DATABASE_URL совпадает с рабочей базой "
                f"({url}) — заведите отдельную, например .../gals_test",
                pytrace=False,
            )
        yield url
        return

    url, container = _testcontainer_url()
    if url is None:
        pytest.skip(
            "нет БД для тестов: задайте GALS_TEST_DATABASE_URL "
            "или установите testcontainers и запустите Docker"
        )
    try:
        yield url
    finally:
        container.stop()


@pytest.fixture(scope="session")
def engine(database_url):
    """Движок и накатанная схема — один раз на прогон."""
    from alembic import command
    from alembic.config import Config

    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def fake_terrain_provider(monkeypatch):
    """Автосюит не ходит в сеть за рельефом (см. uav_planner.terrain) —
    ``ConstantElevationProvider`` вместо реального Open Topo Data везде, где
    ``plan_service``/``safety_service`` берут провайдер через
    ``services.terrain_service.default_elevation_provider``.

    Рельеф остается ВКЛЮЧЕН (плоский, 0 м) — так тесты покрывают код облета
    рельефа (3D-координаты в track_geojson, height_agl_m), а не только путь
    «рельеф выключен». Тест, которому явно нужен отключенный рельеф или
    иной профиль, подменяет фикстуру локально через ``monkeypatch`` ещё раз.
    """
    from uav_planner.terrain import ConstantElevationProvider

    provider = ConstantElevationProvider(height_m=0.0)
    monkeypatch.setattr("uav_planner.services.terrain_service.default_elevation_provider", lambda: provider)
    return provider


@pytest.fixture(autouse=True)
def eager_celery():
    """Задачи выполняются синхронно, без Redis.

    ``task_eager_propagates=False`` — специально: падение задачи должно давать
    строку ``plan_jobs`` со статусом «Ошибка», как в проде, а не всплывать
    исключением в тело теста.
    """
    from uav_planner.jobs.celery_app import celery_app

    previous = (
        celery_app.conf.task_always_eager,
        celery_app.conf.task_eager_propagates,
    )
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    try:
        yield celery_app
    finally:
        celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates = previous


@pytest.fixture
def db(engine):
    """Сессия теста внутри внешней транзакции, которая откатывается в конце."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    token = db_session.bind_session(session)
    # Фоновый код, открывающий сессию сам, должен получить эту же.
    db_session.set_session_factory(lambda: session)
    try:
        yield session
    finally:
        db_session.unbind_session(token)
        db_session.set_session_factory(None)
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db) -> TestClient:
    from uav_planner.api.app import app

    return TestClient(app)


def pytest_collection_modifyitems(items):
    """Тесты, работающие через API, требуют БД — помечаем их автоматически."""
    for item in items:
        if "client" in getattr(item, "fixturenames", ()) or "db" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)
