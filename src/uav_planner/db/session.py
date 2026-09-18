"""Движок, фабрика сессий и текущая сессия запроса.

Сессия передается не аргументом через все сервисы, а через ``ContextVar``,
привязанный к запросу (middleware), к job'у воркера или к тесту. Компромисс
осознанный: явная передача чище, но она изменила бы сигнатуры всех функций
сервисов и роутеров разом; контекстная сессия дает тот же слой хранения при
нулевом изменении контракта. Единственное правило — границу контекста задает
тот, кто владеет транзакцией (``session_scope``), а не сервис.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from uav_planner.config import Settings, get_settings

_engine: Engine | None = None
_session_factory: Callable[[], Session] | None = None
_current_session: ContextVar[Session | None] = ContextVar("gals_session", default=None)


class NoSessionError(RuntimeError):
    """Обращение к БД вне контекста сессии — ошибка вызывающего кода."""


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        if not settings.database_configured:
            raise RuntimeError(
                "GALS_DATABASE_URL не задан — подключение к БД не сконфигурировано"
            )
        _engine = create_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            echo=settings.database_echo_sql,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> Callable[[], Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def set_session_factory(factory: Callable[[], Session] | None) -> None:
    """Подмена фабрики сессий (тесты, eager-режим фоновых задач)."""
    global _session_factory
    _session_factory = factory


def reset_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def current_session() -> Session:
    """Сессия текущего контекста. Ее открывает владелец транзакции, не сервис."""
    session = _current_session.get()
    if session is None:
        raise NoSessionError(
            "нет активной сессии БД: оберните вызов в session_scope() "
            "или выполняйте его внутри HTTP-запроса"
        )
    return session


def has_session() -> bool:
    return _current_session.get() is not None


def bind_session(session: Session):
    """Привязать готовую сессию к контексту. Возвращает токен для reset."""
    return _current_session.set(session)


def unbind_session(token) -> None:
    _current_session.reset(token)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакция: коммит при успехе, откат при исключении.

    Вложенный вызов переиспользует уже привязанную сессию и не коммитит ее —
    границу транзакции задает самый внешний контекст.
    """
    existing = _current_session.get()
    if existing is not None:
        yield existing
        return

    session = get_session_factory()()
    token = _current_session.set(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        _current_session.reset(token)
        session.close()


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI для точечного доступа к сессии в роутере."""
    with session_scope() as session:
        yield session
