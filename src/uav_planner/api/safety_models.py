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
    # Стабильный в пределах отчета идентификатор ("geozones__0") — по нему
    # оператор отмечает нарушение принятым (игнорирует), см. safety_service.
    # set_violation_ignored и static/index.html.
    id: str
    message: str
    # Координаты «опасного момента» в WGS-84 для отметки на карте (БЕЗ.ФТ.4 + подсветка
    # при наведении, см. static/index.html) — отсутствуют, если для данного нарушения нет
    # осмысленной единственной точки.
    lat: float | None = None
    lon: float | None = None
    # Оператор осознанно принял риск и отметил нарушение как проигнорированное —
    # если так отмечены все нарушения отчета, план можно подтвердить в обход
    # ЭКС.ФТ.2, см. SafetyReport.violations_acknowledged и plan_service.confirm_plan.
    ignored: bool = False


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
    # True, только если в отчете есть хотя бы одно нарушение и оператор отметил
    # ВСЕ их как проигнорированные (ViolationOut.ignored) — тогда план можно
    # подтвердить, несмотря на нарушения (расширение ЭКС.ФТ.2 по запросу
    # пользователя, вне исходного текста требования — см. main/README.md).
    violations_acknowledged: bool = False
