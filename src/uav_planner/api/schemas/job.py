"""Pydantic-схемы фонового расчета — ПЛН.ФТ.5 (индикатор прогресса и отмена).

Контракт зафиксирован до фронтенда намеренно (см. план обертки, раздел о
связанности экранов 5 и 7): SPA держит двойной путь ответа
``200 PlanSummary | 202 JobAccepted`` и опрашивает ``GET /api/plan-jobs/{id}``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class JobInfo(BaseModel):
    id: str
    task_id: str
    kind: str  # 'plan' | 'safety'
    # Ровно строки ПЛН.ФТ.5: «В очереди», «Выполняется», «Завершен»,
    # «Остановлен по лимиту времени», «Ошибка», «Отменен».
    status: str
    # Человекочитаемая стадия конвейера («Построение галсов»), не код.
    stage: Optional[str] = None
    progress: int = 0
    error: Optional[str] = None
    error_code: Optional[str] = None
    cancel_requested: bool = False
    plan_id: Optional[str] = None  # вход: план, который попросили проверить
    result_plan_id: Optional[str] = None  # выход: рассчитанный (или пересчитанный) план
    result_report_id: Optional[str] = None  # выход: отчет проверки безопасности
    auto_recalc_count: int = 0  # БЕЗ.ФТ.3
    queued_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None or self.status not in ("В очереди", "Выполняется")


class JobAccepted(BaseModel):
    """Тело ответа 202: расчет не успел уложиться в окно синхронного ожидания."""

    job: JobInfo
