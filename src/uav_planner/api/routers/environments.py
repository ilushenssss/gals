"""HTTP-маршруты модуля «Обстановка» — ОБС.ФТ.5, ОБС.ФТ.8-10.

Формат файла — GeoJSON FeatureCollection или KML (см.
``uav_planner.kml.environment`` про то, какие именно виды KML распознаются
и как они превращаются в тот же самый GeoJSON перед проверкой)."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from uav_planner.kml import kml_to_environment_geojson
from uav_planner.services import environment_service as service
from uav_planner.api.schemas.environment import EnvironmentDetail, EnvironmentSummary

router = APIRouter(prefix="/api", tags=["environments"])


def _looks_like_kml(raw: bytes) -> bool:
    head = raw[:512].lstrip()
    return head.startswith(b"<?xml") or head.startswith(b"<kml")


@router.post("/environments", response_model=EnvironmentSummary)
async def upload_environment(
    name: str = Form(...),
    file: UploadFile = File(...),
) -> EnvironmentSummary:
    raw = await file.read()
    if _looks_like_kml(raw):
        try:
            geojson = kml_to_environment_geojson(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"не удалось разобрать KML-файл: {exc}") from exc
    else:
        try:
            geojson = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"файл не является корректным JSON: {exc}") from exc

    try:
        return service.validate_and_store(geojson, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/environments", response_model=list[EnvironmentSummary])
def list_environments() -> list[EnvironmentSummary]:
    return service.list_environments()


@router.get("/environments/{environment_id}", response_model=EnvironmentDetail)
def get_environment(environment_id: str) -> EnvironmentDetail:
    try:
        return service.get_environment(environment_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="обстановка не найдена")
