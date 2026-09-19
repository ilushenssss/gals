"""Pydantic-схемы модуля «Проверка безопасности» — см.
docs/trebovania/Проверка_безопасности.md."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# БЕЗ.ФТ.5 также описывает промежуточный статус "В процессе автоматического
# пересчета" (желтый индикатор). В этой версии конвейер БЕЗ.ФТ.3 выполняется
# синхронно в одном вызове API, поэтому промежуточное состояние не наблюдаемо
# и не хранится — к моменту получения ответа пересчет (если был) уже завершен.
Status = Literal["Пройдена", "Есть нарушения"]


class ViolationOut(BaseModel):
    message: str
    # Координаты «опасного момента» в WGS-84 для отметки на карте (БЕЗ.ФТ.4 + подсветка
    # при наведении, см. static/index.html) — отсутствуют, если для данного нарушения нет
    # осмысленной единственной точки.
    lat: float | None = None
    lon: float | None = None


class SafetyCheckOut(BaseModel):
    name: str
    label: str
    passed: bool
    violations: list[ViolationOut] = []


class SafetyReport(BaseModel):
    id: str
    plan_id: str  # версия плана, к которой относится этот отчет (может отличаться от запрошенной — после автопересчета, см. safety_service.check_plan)
    task_id: str
    created_at: datetime
    status: Status
    checks: list[SafetyCheckOut]
    auto_recalc_count: int  # число автоматических пересчетов, потраченных на текущую версию задачи (БЕЗ.ФТ.3, максимум 3)
