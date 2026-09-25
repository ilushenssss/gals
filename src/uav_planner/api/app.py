"""Точка входа веб-сервиса: только API.

Приложение собирается фабрикой ``create_app(settings)`` — так тесты могут
поднять его с другими настройками, а модульный ``app`` сохранен для
``uvicorn uav_planner.api.app:app`` и существующих тестов на ``TestClient``.

Фронтенд этот процесс не отдает: интерфейс — React-SPA из ``web/`` за nginx
(сервис ``web`` в compose), который проксирует ``/api`` сюда. Origin один,
поэтому CORS не нужен (список ``cors_origins`` по умолчанию пуст и остается на
случай, когда фронтенд поднимают отдельно). Прежний vanilla-JS интерфейс
``api/static/`` снят 25.09.2026 после закрытия ``web/PARITY.md``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from uav_planner.config import Settings, get_settings
from uav_planner.logging_setup import configure_logging

from .middleware import DbSessionMiddleware, RequestContextMiddleware

from .routers.environments import router as environments_router
from .routers.fleet import router as fleet_router
from .routers.health import router as health_router
from .routers.plan_jobs import router as plan_jobs_router
from .routers.plans import router as plans_router
from .routers.safety import router as safety_router
from .routers.tasks import router as tasks_router

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Галс — планировщик БВС",
        version="0.1.0",
        description=(
            "Планирование и распределение беспилотных авиационных работ: обстановка, парк БВС, "
            "задача, расчет плана (фоновая работа), независимая проверка безопасности, "
            "подтверждение и экспорт. Длительные запросы (`POST /api/plans`, "
            "`POST /api/safety-checks`) отвечают `200` с результатом, если успели за `wait_s`, "
            "иначе `202` с объектом работы (`GET /api/plan-jobs/{id}`)."
        ),
    )
    app.state.settings = settings

    # Фронтенд отдает этот же процесс — один origin, поэтому список по
    # умолчанию пуст и middleware не навешивается.
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

    # Добавляется последним и поэтому оказывается самым внешним: идентификатор
    # запроса должен попасть в контекст раньше всех, чтобы его несли и записи
    # об открытии сессии, и запись о необработанной ошибке.
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health_router)
    app.include_router(environments_router)
    app.include_router(tasks_router)
    app.include_router(fleet_router)
    app.include_router(plans_router)
    app.include_router(plan_jobs_router)
    app.include_router(safety_router)

    return app


app = create_app()
