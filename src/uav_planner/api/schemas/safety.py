"""Pydantic-схемы модуля «Проверка безопасности» — см.
docs/trebovania/Проверка_безопасности.md."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Status = Literal["Пройдена", "Есть нарушения"]


class ViolationOut(BaseModel):
    """Одно нарушение проверки безопасности.

    Прежде это была строка. Теперь у нарушения есть стабильный в пределах
    отчёта идентификатор (``geozones__0``) и координата «опасного момента»:
    по идентификатору оператор отмечает нарушение принятым, по координате
    интерфейс ставит маркер на карту (БЕЗ.ФТ.4, ИНТ.ФТ.14).
    """

    id: str
    message: str
    # Отсутствуют, если у нарушения нет осмысленной единственной точки
    # (например, сводка «...и ещё N нарушений» или непокрытая доля области).
    lat: float | None = None
    lon: float | None = None
    # Оператор осознанно принял риск. Если так отмечены ВСЕ нарушения отчёта,
    # план можно подтвердить в обход ЭКС.ФТ.2 — см. violations_acknowledged.
    ignored: bool = False


class SafetyCheckOut(BaseModel):
    name: str
    label: str
    passed: bool
    violations: list[ViolationOut] = []
    # БЕЗ.ФТ.4: «предлагаемые варианты решения» — что сделать оператору (или
    # что уже сделал автопересчет) при нарушении этого критерия. Пусто, если
    # критерий пройден.
    recommendations: list[str] = []


class SafetyReport(BaseModel):
    id: str
    plan_id: str  # версия плана, к которой относится этот отчет (может отличаться от запрошенной — после автопересчета, см. safety_service.check_plan)
    task_id: str
    created_at: datetime
    status: Status
    checks: list[SafetyCheckOut]
    auto_recalc_count: int  # число автоматических пересчетов, потраченных на текущую версию задачи (БЕЗ.ФТ.3, максимум 3)
    # True, только если нарушения есть и оператор отметил принятыми все.
    # Вычисляется из самих нарушений, отдельно не хранится — иначе флаг и
    # список могли бы разойтись.
    violations_acknowledged: bool = False
