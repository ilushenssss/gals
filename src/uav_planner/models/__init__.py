"""ORM-модели. Импортируются миграциями, чтобы все таблицы попали в metadata."""

from uav_planner.db.base import Base

from .environment import Environment, EnvironmentFeature, EnvironmentIssue
from .fleet import FleetInstance, FleetIssue, FleetUpload
from .job import PlanJob
from .plan import Plan, PlanSortie
from .safety import SafetyAttemptCounter, SafetyCheck, SafetyReport
from .task import Task

__all__ = [
    "Base",
    "Environment", "EnvironmentFeature", "EnvironmentIssue",
    "FleetUpload", "FleetInstance", "FleetIssue",
    "Task",
    "Plan", "PlanSortie",
    "PlanJob",
    "SafetyReport", "SafetyCheck", "SafetyAttemptCounter",
]
