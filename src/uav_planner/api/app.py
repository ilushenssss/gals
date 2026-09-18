"""Точка входа веб-сервиса: API и (пока) статический фронтенд первой версии.

Приложение собирается фабрикой ``create_app(settings)`` — так тесты могут
поднять его с другими настройками, а модульный ``app`` сохранен для
``uvicorn uav_planner.api.app:app`` и существующих тестов на ``TestClient``.

Статика отдается этим же процессом, только пока ``GALS_SERVE_STATIC=true``. В
целевой схеме SPA отдает контейнер ``web`` (nginx), и флаг выключается.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from uav_planner.config import Settings, get_settings

from .middleware import DbSessionMiddleware

from .routers.environments import router as environments_router
from .routers.fleet import router as fleet_router
from .routers.health import router as health_router
from .routers.plans import router as plans_router
from .routers.safety import router as safety_router
from .routers.tasks import router as tasks_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(title="Галс — планировщик БВС", version="0.1.0")
    app.state.settings = settings

    # Фронтенд и API ходят через один origin (nginx или dev-proxy Vite),
    # поэтому список по умолчанию пуст и middleware не навешивается.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Сессия БД живет ровно один запрос: коммит после успешного ответа,
    # откат при исключении.
    app.add_middleware(DbSessionMiddleware, enabled=settings.database_configured)

    app.include_router(health_router)
    app.include_router(environments_router)
    app.include_router(tasks_router)
    app.include_router(fleet_router)
    app.include_router(plans_router)
    app.include_router(safety_router)

    # Монтируется последним: StaticFiles на "/" перехватывает все, что не
    # разобрали роутеры выше.
    if settings.serve_static and STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")

    return app


app = create_app()
