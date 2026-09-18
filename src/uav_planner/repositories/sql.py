"""Репозитории поверх PostgreSQL/PostGIS.

Интерфейс тот же, что был у реализации в памяти, — сервисы и роутеры не
изменились. Сессию репозитории берут из контекста (``db.session``), границу
транзакции задает вызывающий: HTTP-запрос (middleware) или фоновая задача.

Отсутствующая запись — ``KeyError``: роутеры уже переводят его в 404, и менять
этот контракт при смене хранилища незачем.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from uav_planner.api.schemas.environment import EnvironmentDetail
from uav_planner.api.schemas.fleet import FleetDetail
from uav_planner.api.schemas.plan import PlanDetail
from uav_planner.api.schemas.safety import SafetyReport
from uav_planner.api.schemas.task import TaskDetail
from uav_planner.db.session import current_session
from uav_planner.models.environment import Environment
from uav_planner.models.fleet import FleetUpload
from uav_planner.models.plan import Plan
from uav_planner.models.safety import SafetyAttemptCounter, SafetyReport as SafetyReportRow
from uav_planner.models.task import Task

from . import mappers
from .mappers import as_uuid


def _uuid_or_none(value: str) -> uuid.UUID | None:
    """Чужой идентификатор в пути — не ошибка сервера, а просто «не найдено»."""
    try:
        return as_uuid(value)
    except (ValueError, AttributeError, TypeError):
        return None


class EnvironmentRepository:
    def put(self, environment_id: str, detail: EnvironmentDetail) -> EnvironmentDetail:
        session = current_session()
        session.add(mappers.environment_to_rows(detail))
        session.flush()
        return detail

    def get(self, environment_id: str) -> EnvironmentDetail:
        key = _uuid_or_none(environment_id)
        row = current_session().get(Environment, key) if key else None
        if row is None:
            raise KeyError(environment_id)
        return mappers.environment_from_row(row)

    def find(self, environment_id: str) -> EnvironmentDetail | None:
        try:
            return self.get(environment_id)
        except KeyError:
            return None

    def list_newest_first(self) -> list[EnvironmentDetail]:
        rows = current_session().scalars(
            select(Environment).order_by(Environment.uploaded_at.desc())
        ).all()
        return [mappers.environment_from_row(r) for r in rows]


class TaskRepository:
    def put(self, task_id: str, detail: TaskDetail) -> TaskDetail:
        session = current_session()
        row = session.get(Task, as_uuid(task_id))
        if row is None:
            row = Task()
            session.add(mappers.apply_task(row, detail))
        else:
            mappers.apply_task(row, detail)
        session.flush()
        return detail

    def get(self, task_id: str) -> TaskDetail:
        key = _uuid_or_none(task_id)
        row = current_session().get(Task, key) if key else None
        if row is None:
            raise KeyError(task_id)
        return mappers.task_from_row(row)

    def find(self, task_id: str) -> TaskDetail | None:
        try:
            return self.get(task_id)
        except KeyError:
            return None

    def list_by_environment(self, environment_id: str | None) -> list[TaskDetail]:
        stmt = select(Task).order_by(Task.updated_at.desc())
        if environment_id is not None:
            key = _uuid_or_none(environment_id)
            if key is None:
                return []
            stmt = stmt.where(Task.environment_id == key)
        return [mappers.task_from_row(r) for r in current_session().scalars(stmt).all()]


class FleetRepository:
    """ПБС.ФТ.11: текущая загрузка одна, повторная заменяет предыдущую.

    Прежняя загрузка не удаляется, а теряет признак ``is_current`` — история
    нужна, чтобы план мог сослаться на состав парка, по которому его считали.
    """

    def set_current(self, detail: FleetDetail) -> FleetDetail:
        session = current_session()
        for previous in session.scalars(
            select(FleetUpload).where(FleetUpload.is_current.is_(True))
        ).all():
            previous.is_current = False
        session.flush()
        session.add(mappers.fleet_to_rows(detail))
        session.flush()
        return detail

    def get_current(self) -> FleetDetail | None:
        row = current_session().scalars(
            select(FleetUpload).where(FleetUpload.is_current.is_(True))
        ).first()
        return None if row is None else mappers.fleet_from_row(row)


class PlanRepository:
    def next_version(self, task_id: str) -> int:
        key = _uuid_or_none(task_id)
        if key is None:
            return 1
        current = current_session().scalar(
            select(func.max(Plan.version)).where(Plan.task_id == key)
        )
        return (current or 0) + 1

    def add(self, detail: PlanDetail) -> PlanDetail:
        session = current_session()
        session.add(mappers.plan_to_rows(detail))
        session.flush()
        return detail

    def get(self, plan_id: str) -> PlanDetail:
        key = _uuid_or_none(plan_id)
        row = current_session().get(Plan, key) if key else None
        if row is None:
            raise KeyError(plan_id)
        return mappers.plan_from_row(row)

    def find(self, plan_id: str) -> PlanDetail | None:
        try:
            return self.get(plan_id)
        except KeyError:
            return None

    def list_by_task_newest_first(self, task_id: str) -> list[PlanDetail]:
        key = _uuid_or_none(task_id)
        if key is None:
            return []
        rows = current_session().scalars(
            select(Plan).where(Plan.task_id == key).order_by(Plan.version.desc())
        ).all()
        return [mappers.plan_from_row(r) for r in rows]


class SafetyRepository:
    def add_report(self, plan_ids: list[str], report: SafetyReport) -> SafetyReport:
        """Один отчет, а не два.

        В версии на словарях отчет клался под двумя ключами — под запрошенным
        планом и под пересчитанным (БЕЗ.ФТ.3 меняет версию плана). Здесь это
        одна строка с двумя ссылками, поэтому отчеты не задваиваются.
        """
        session = current_session()
        requested = plan_ids[0] if plan_ids else report.plan_id
        session.add(mappers.safety_report_to_rows(report, requested))
        session.flush()
        return report

    def latest_report(self, plan_id: str) -> SafetyReport | None:
        key = _uuid_or_none(plan_id)
        if key is None:
            return None
        row = current_session().scalars(
            select(SafetyReportRow)
            .where(
                or_(
                    SafetyReportRow.plan_id == key,
                    SafetyReportRow.requested_plan_id == key,
                )
            )
            .order_by(SafetyReportRow.created_at.desc(), SafetyReportRow.id.desc())
        ).first()
        return None if row is None else mappers.safety_report_from_row(row)

    def get_attempts(self, task_id: str, task_version: int) -> int:
        key = _uuid_or_none(task_id)
        if key is None:
            return 0
        row = current_session().get(SafetyAttemptCounter, (key, task_version))
        return 0 if row is None else row.attempts

    def set_attempts(self, task_id: str, task_version: int, attempts: int) -> None:
        stmt = (
            pg_insert(SafetyAttemptCounter)
            .values(
                task_id=as_uuid(task_id),
                task_version=task_version,
                attempts=attempts,
                updated_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=[SafetyAttemptCounter.task_id, SafetyAttemptCounter.task_version],
                set_={"attempts": attempts, "updated_at": datetime.now(timezone.utc)},
            )
        )
        current_session().execute(stmt)


environments = EnvironmentRepository()
tasks = TaskRepository()
fleet = FleetRepository()
plans = PlanRepository()
safety = SafetyRepository()
