"""Слой репозиториев: единственное место, знающее, где лежат данные.

Реализация — PostgreSQL + PostGIS (``sql.py``); сессию репозитории берут из
контекста, границу транзакции задает вызывающий (HTTP-запрос или фоновая
задача). Сервисы работают с репозиториями через этот модуль и про SQL ничего
не знают.
"""

from uav_planner.db.session import current_session

from .sql import environments, fleet, plans, safety, tasks

__all__ = ["environments", "fleet", "plans", "safety", "tasks", "reset_all"]


def reset_all() -> None:
    """Удалить все данные предметной области.

    Нужна вспомогательным сценариям (например, ручной переигровке сцены);
    тесты изолируются откатом транзакции, а не этим вызовом.
    """
    from uav_planner.models import Base

    session = current_session()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.flush()
