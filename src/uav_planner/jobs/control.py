"""Управляющие команды воркеру: снятие задачи из очереди и жесткая остановка.

Вынесено в отдельный модуль, потому что у этих вызовов ровно одна тонкость, и
она повторяется в двух местах: ``celery_app.control`` ходит в брокер, а брокер
может быть недоступен (или отсутствовать вовсе — eager-режим тестов). Отмена
при этом обязана оставаться успешной: кооперативный флаг в ``plan_jobs`` уже
поставлен, и он работает сам по себе — ``revoke`` лишь ускоряет развязку и
снимает работу, до которой воркер еще не дошел.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app, is_eager

log = logging.getLogger(__name__)


def revoke_task(celery_task_id: str | None, *, terminate: bool = False) -> bool:
    """Снять задачу из очереди (или прервать выполняющуюся). False — не удалось."""
    if not celery_task_id or is_eager():
        return False
    try:
        # SIGUSR1 приходит в задачу как SoftTimeLimitExceeded, то есть обычным
        # исключением: блок обработки исхода в tasks.py отрабатывает, и строка
        # работы не остается висеть в «Выполняется».
        celery_app.control.revoke(
            celery_task_id, terminate=terminate, signal="SIGUSR1" if terminate else None
        )
        return True
    except Exception:  # noqa: BLE001 — недоступный брокер не должен ломать отмену
        log.warning("не удалось отправить revoke задаче %s", celery_task_id, exc_info=True)
        return False
