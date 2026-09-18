"""Публикация прогресса и кооперативная отмена фонового расчета (ПЛН.ФТ.5).

Два свойства, ради которых это отдельный модуль, а не пара строк в задаче:

1. **Своя короткая транзакция на каждый тик.** Прогресс, записанный внутри
   длинной транзакции работы, не виден никому до ее конца — то есть до конца
   получасового расчета. ``ProgressReporter`` открывает сессию, пишет и сразу
   коммитит.
2. **Отмена — тем же запросом.** ``UPDATE ... RETURNING cancel_requested``
   стоит ровно один round-trip, поэтому проверка флага пристегнута к каждому
   обновлению прогресса и не требует отдельного опроса БД в цикле.

Стадии и веса взяты из плана обертки. Важно, что оба цикла, которые съедают
время на сцене 100 км²/10 БВС (по ячейкам и по бортам), лежат в оркестраторе
``plan_service``, а не внутри чистых математических модулей, — поэтому честный
прогресс достигается без единой правки математики.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update

from uav_planner.db.session import short_session_scope
from uav_planner.models.job import PlanJob

# (код стадии -> человекочитаемое имя, процент на входе в стадию)
STAGES: dict[str, tuple[str, int]] = {
    "load": ("Загрузка исходных данных", 3),
    "model": ("Подбор модели БВС и камеры", 6),
    "survey_geometry": ("Геометрия съемки", 10),
    "working_area": ("Построение рабочей области", 20),
    "decomposition": ("Декомпозиция области", 30),
    "tracks": ("Построение галсов", 30),  # 30 -> 60 по ячейкам
    "assignment": ("Распределение по БВС", 75),
    "schedule": ("Расписание вылетов", 75),  # 75 -> 92 по бортам
    "save": ("Сохранение плана", 96),
    # Проверка безопасности (kind='safety'): БЕЗ.ФТ.2 и БЕЗ.ФТ.3/ФТ.5.
    "safety_check": ("Проверка безопасности", 10),
    "safety_recalc": ("В процессе автоматического пересчета", 40),
    "safety_save": ("Сохранение отчета", 96),
}

TRACKS_SPAN = (30, 60)
SCHEDULE_SPAN = (75, 92)


class JobCancelled(Exception):
    """Оператор отменил расчет (ПЛН.ФТ.5). Не ошибка — штатный выход."""


class ProgressReporter:
    """Пишет стадию и процент работы; на каждом тике сверяет флаг отмены.

    ``no_op`` нужен синхронному пути (``wait_s`` истек не у всех вызовов —
    существующие тесты и отладочные сценарии зовут сервисы напрямую): тогда
    репортер ничего не пишет и никогда не отменяет.
    """

    def __init__(self, job_id: str | None) -> None:
        self.job_id = uuid.UUID(job_id) if job_id else None
        self._last_percent = -1

    @property
    def no_op(self) -> bool:
        return self.job_id is None

    def stage(self, code: str, percent: int | None = None) -> None:
        label, default_percent = STAGES[code]
        self.publish(label, default_percent if percent is None else percent)

    def span(self, code: str, done: int, total: int, span: tuple[int, int]) -> None:
        """Прогресс внутри цикла: линейная интерполяция между границами стадии."""
        low, high = span
        fraction = 0.0 if total <= 0 else min(max(done / total, 0.0), 1.0)
        self.stage(code, int(low + (high - low) * fraction))

    def publish(self, stage: str, percent: int) -> None:
        if self.no_op:
            return
        percent = max(0, min(100, percent))
        # Экономия round-trip'ов: внутри цикла по 500 галсам процент меняется
        # редко, а heartbeat нужен не чаще раза в тик прогресса.
        if percent == self._last_percent:
            return
        self._last_percent = percent
        self._write(stage=stage, progress=percent)

    def heartbeat(self) -> None:
        """Обновить только отметку жизни (и проверить отмену)."""
        if self.no_op:
            return
        self._write()

    def check_cancelled(self) -> None:
        if self.no_op:
            return
        self._write()

    def _write(self, **values) -> None:
        now = datetime.now(timezone.utc)
        with short_session_scope() as session:
            cancel_requested = session.execute(
                update(PlanJob)
                .where(PlanJob.id == self.job_id)
                .values(heartbeat_at=now, **values)
                .returning(PlanJob.cancel_requested)
            ).scalar_one_or_none()
        if cancel_requested:
            raise JobCancelled()
