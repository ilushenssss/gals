"""Точка входа веб-сервиса: API + статический фронтенд первой версии интерфейса.

Запуск: ``uvicorn uav_planner.api.app:app --reload`` из каталога ``main`` (после
``pip install -e ".[dev]"``). Интерфейс — http://127.0.0.1:8000/
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .export_routes import router as export_router
from .fleet_routes import router as fleet_router
from .plan_routes import router as plans_router
from .routes import router as environments_router
from .safety_routes import router as safety_router
from .task_routes import router as tasks_router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Галс — планировщик БВС", version="0.1.0")
app.include_router(environments_router)
app.include_router(tasks_router)
app.include_router(fleet_router)
app.include_router(plans_router)
app.include_router(safety_router)
app.include_router(export_router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
