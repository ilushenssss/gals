"""Health-эндпоинты для docker compose и балансировщика.

``/api/health`` — liveness: процесс жив, зависимости не проверяются (иначе
недоступная БД перезапускала бы контейнер вместо того, чтобы просто вывести его
из ротации). ``/api/health/ready`` — readiness: проверяет БД и Redis, но только
те, что сконфигурированы.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from uav_planner.config import get_settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready() -> JSONResponse:
    settings = get_settings()
    checks: dict[str, str] = {}

    if settings.database_configured:
        try:
            from sqlalchemy import text

            from uav_planner.db.session import get_engine

            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 — текст ошибки нужен оператору
            checks["database"] = f"error: {exc}"
    else:
        checks["database"] = "not configured"

    try:
        import redis

        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    ok = all(v == "ok" or v == "not configured" for v in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )
