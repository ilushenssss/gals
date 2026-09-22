"""ASGI-middleware запроса: сессия БД и контекст журнала.

Реализовано как чистое ASGI-приложение, а не через ``BaseHTTPMiddleware``:
контекстная переменная, установленная здесь, должна быть видна и в
синхронных обработчиках, которые Starlette исполняет в пуле потоков (anyio
копирует контекст в поток, но не возвращает изменения обратно — поэтому
устанавливать ее в зависимости с ``yield`` нельзя).

Транзакция на запрос: коммит после успешного ответа, откат при исключении.
Если сессия уже привязана к контексту (тесты подставляют свою, внутри
откатываемой транзакции), middleware ничего не делает.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from uav_planner.db.session import bind_session, get_session_factory, has_session, unbind_session
from uav_planner.logging_setup import log_context

log = logging.getLogger(__name__)


class DbSessionMiddleware:
    def __init__(self, app: ASGIApp, enabled: bool = True) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled or has_session():
            await self.app(scope, receive, send)
            return

        session = get_session_factory()()
        token = bind_session(session)
        try:
            await self.app(scope, receive, send)
            await run_in_threadpool(session.commit)
        except Exception:
            await run_in_threadpool(session.rollback)
            raise
        finally:
            unbind_session(token)
            await run_in_threadpool(session.close)


class RequestContextMiddleware:
    """Идентификатор запроса в контексте журнала и одна строка на запрос.

    Тоже чистое ASGI-приложение, и по той же причине, что ``DbSessionMiddleware``:
    контекстная переменная, установленная в ``BaseHTTPMiddleware``, не видна
    синхронным обработчикам, которые Starlette исполняет в пуле потоков.

    Идентификатор берется из заголовка ``X-Request-Id``, если клиент (или
    reverse proxy) его прислал, иначе выдается свой; он же возвращается в
    ответе, поэтому по строке в журнале браузера находится строка в журнале
    сервиса. Внутрь запроса он попадает через ``log_context``, и его несут все
    записи, сделанные обработчиком, сервисами и репозиториями.

    Строка пишется по завершении, а не на приеме: только тогда известны исход и
    длительность. Прием отмечается на уровне DEBUG — он нужен, когда запрос
    завис и завершения не будет. Проверки состояния (``/api/health``) идут
    отдельным уровнем: docker опрашивает их каждые 15 с, и в INFO они
    вытеснили бы все остальное.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _header(scope, b"x-request-id") or uuid.uuid4().hex[:16]
        method = scope.get("method", "")
        path = scope.get("path", "")
        level = logging.DEBUG if path.startswith("/api/health") else logging.INFO
        status = 500

        async def send_wrapper(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", request_id.encode("ascii", "ignore"))
                )
            await send(message)

        started = time.perf_counter()
        with log_context(request_id=request_id):
            log.debug("запрос принят", extra={"method": method, "path": path})
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception:
                log.exception(
                    "запрос завершился необработанной ошибкой",
                    extra={"method": method, "path": path},
                )
                raise
            finally:
                log.log(
                    level,
                    "запрос обработан",
                    extra={
                        "method": method,
                        "path": path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key == name:
            return value.decode("latin-1").strip() or None
    return None
