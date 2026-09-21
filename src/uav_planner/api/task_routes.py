"""HTTP-маршруты модуля «Задача» — ЗАД.ФТ.6-12."""

from __future__ import annotations

import json
from datetime import date, time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import task_service
from .task_models import TaskDetail, TaskSummary
from .task_service import TaskConflictError, TaskNotEditableError, TaskValidationError

router = APIRouter(prefix="/api", tags=["tasks"])


def _parse_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


async def _read_area_geojson(area_file: UploadFile) -> dict:
    raw = await area_file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"файл области облета не является корректным JSON: {exc}") from exc
    # Допускаем как Feature, так и «голую» геометрию.
    return data.get("geometry", data) if data.get("type") == "Feature" else data


@router.post("/tasks", response_model=TaskSummary)
async def create_task(
    name: str = Form(...),
    environment_id: str = Form(...),
    fleet_id: str = Form(...),
    survey_type: str = Form(...),
    gsd_cm: float = Form(...),
    work_date: date = Form(...),
    window_start: str | None = Form(None),
    window_end: str | None = Form(None),
    wind_speed_ms: float | None = Form(None),
    cloud_cover_pct: float | None = Form(None),
    criterion_mode: str = Form(...),
    criterion_alpha: float | None = Form(None),
    area_file: UploadFile = File(...),
) -> TaskSummary:
    area_geojson = await _read_area_geojson(area_file)
    try:
        return task_service.create_task(
            name=name, environment_id=environment_id, fleet_id=fleet_id, survey_type=survey_type, gsd_cm=gsd_cm,
            work_date=work_date, window_start=_parse_time(window_start), window_end=_parse_time(window_end),
            wind_speed_ms=wind_speed_ms, cloud_cover_pct=cloud_cover_pct, criterion_mode=criterion_mode,
            criterion_alpha=criterion_alpha, area_geojson=area_geojson,
        )
    except TaskValidationError as exc:
        raise HTTPException(status_code=400, detail=[i.model_dump() for i in exc.issues]) from exc


@router.put("/tasks/{task_id}", response_model=TaskSummary)
async def update_task(
    task_id: str,
    expected_version: int = Form(...),
    name: str = Form(...),
    survey_type: str = Form(...),
    gsd_cm: float = Form(...),
    work_date: date = Form(...),
    window_start: str | None = Form(None),
    window_end: str | None = Form(None),
    wind_speed_ms: float | None = Form(None),
    cloud_cover_pct: float | None = Form(None),
    criterion_mode: str = Form(...),
    criterion_alpha: float | None = Form(None),
    area_file: UploadFile = File(...),
) -> TaskSummary:
    area_geojson = await _read_area_geojson(area_file)
    try:
        return task_service.update_task(
            task_id, expected_version, name=name, survey_type=survey_type, gsd_cm=gsd_cm,
            work_date=work_date, window_start=_parse_time(window_start), window_end=_parse_time(window_end),
            wind_speed_ms=wind_speed_ms, cloud_cover_pct=cloud_cover_pct, criterion_mode=criterion_mode,
            criterion_alpha=criterion_alpha, area_geojson=area_geojson,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="задача не найдена")
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TaskNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TaskValidationError as exc:
        raise HTTPException(status_code=400, detail=[i.model_dump() for i in exc.issues]) from exc


@router.get("/tasks", response_model=list[TaskSummary])
def list_tasks(environment_id: str | None = None) -> list[TaskSummary]:
    return task_service.list_tasks(environment_id)


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: str) -> TaskDetail:
    try:
        return task_service.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="задача не найдена")
