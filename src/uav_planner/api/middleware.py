"""ASGI-middleware, открывающее сессию БД на время HTTP-запроса.

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

from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from uav_planner.db.session import bind_session, get_session_factory, has_session, unbind_session


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
