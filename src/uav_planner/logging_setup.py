"""Единая настройка журнала и контекст корреляции.

Три свойства, ради которых это отдельный модуль, а не пара строк в ``app.py``:

1. **Один конфиг на все три роли.** ``api``, ``worker`` и ``beat`` — один образ
   и один формат записи. В проде это JSON по строке на запись (``grep`` по
   полю, а не по тексту), в разработке — читаемая строка. Выбор делает
   ``Settings.log_as_json``, а не место вызова.
2. **Корреляция через ``ContextVar``.** Идентификаторы запроса и работы не
   передаются аргументом в каждую функцию — они живут рядом с сессией БД
   (``db/session.py``), в контексте, и подмешиваются в запись фильтром. Поэтому
   лог математики, репозитория и сервиса несет ``job_id``/``task_id``, хотя эти
   слои о журнале ничего не знают. Половина пользы от журнала получасового
   расчета — в том, чтобы выбрать строки одной работы из общего потока.
3. **Чужие логгеры пишут туда же.** ``uvicorn`` и ``celery`` по умолчанию
   ставят свои обработчики со своим форматом — тогда в потоке живут три разных
   формата сразу. Здесь они разоружаются и отдают записи корню.

``uvicorn.access`` отключен намеренно: строку про принятый запрос пишет
``api/middleware.py`` — с идентификатором запроса, длительностью и исходом, то
есть тем, чего в стандартной строке доступа нет.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from uav_planner.config import Settings, get_settings

_context: ContextVar[dict[str, str]] = ContextVar("gals_log_context", default={})

# Стандартные поля LogRecord: все, чего здесь нет, пришло из ``extra=`` и
# попадает в запись как отдельное поле.
_RESERVED = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info taskName thread threadName gals_context""".split()
)


def get_log_context() -> dict[str, str]:
    return _context.get()


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """Добавить поля корреляции ко всем записям внутри блока.

    Значения ``None`` пропускаются: вызывающему не нужно ветвиться на то, знает
    ли он уже идентификатор задачи.
    """
    extra = {k: str(v) for k, v in values.items() if v is not None}
    token = _context.set({**_context.get(), **extra})
    try:
        yield
    finally:
        _context.reset(token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.gals_context = _context.get()
        return True


def _record_extra(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}


def _timestamp(record: logging.LogRecord) -> str:
    moment = datetime.fromtimestamp(record.created, timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    """Строка JSON на запись: ``ts``, ``level``, ``logger``, ``message`` плюс
    поля контекста и всё, что пришло в ``extra=``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "gals_context", {}) or {})
        payload.update(_record_extra(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # ensure_ascii=False — сообщения русские, экранированный юникод в
        # журнале не читается ни человеком, ни глазами при разборе инцидента.
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Читаемая строка для разработки: время, уровень, логгер, сообщение и
    хвост из полей контекста и ``extra=``."""

    def format(self, record: logging.LogRecord) -> str:
        fields = {**(getattr(record, "gals_context", {}) or {}), **_record_extra(record)}
        tail = " ".join(f"{k}={v}" for k, v in fields.items())
        line = f"{_timestamp(record)} {record.levelname:<7} {record.name} | {record.getMessage()}"
        if tail:
            line = f"{line}  [{tail}]"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


_configured = False


def configure_logging(settings: Settings | None = None, *, force: bool = False) -> None:
    """Настроить корневой логгер. Повторные вызовы игнорируются.

    Вызывается из фабрики приложения (``api/app.py``) и из сигнала
    ``setup_logging`` Celery (``jobs/celery_app.py``) — то есть в каждом из трех
    процессов ровно один раз, до первой записи.
    """
    global _configured
    if _configured and not force:
        return
    settings = settings or get_settings()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_as_json else TextFormatter())
    handler.addFilter(_ContextFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # Разоружаем чужие обработчики: записи должны доходить до корня и получать
    # общий формат, иначе в одном потоке живут три разных.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.app.trace"):
        foreign = logging.getLogger(name)
        foreign.handlers = []
        foreign.propagate = True
    # Свою строку доступа пишет middleware — с идентификатором и исходом.
    logging.getLogger("uvicorn.access").disabled = True

    _configured = True
