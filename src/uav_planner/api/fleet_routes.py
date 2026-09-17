"""HTTP-маршруты модуля «Парк БВС» — ПБС.ФТ.5-11."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from uav_planner.fleet import FLEET_MODELS

from . import fleet_service
from .fleet_models import FleetDetail, FleetSummary, ModelSpecOut

router = APIRouter(prefix="/api", tags=["fleet"])


@router.post("/fleet", response_model=FleetSummary)
async def upload_fleet(file: UploadFile = File(...)) -> FleetSummary:
    raw = await file.read()
    try:
        return fleet_service.validate_and_store(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"не удалось разобрать файл парка: {exc}") from exc


@router.get("/fleet", response_model=FleetDetail)
def get_fleet() -> FleetDetail:
    try:
        return fleet_service.get_fleet()
    except KeyError:
        raise HTTPException(status_code=404, detail="парк БВС не загружен")


@router.get("/fleet/models", response_model=list[ModelSpecOut])
def get_fleet_models() -> list[ModelSpecOut]:
    return [
        ModelSpecOut(
            key=m.key,
            name=m.name,
            uav_type=m.uav_type,
            speed_min_ms=m.speed_ms.min_ms,
            speed_max_ms=m.speed_ms.max_ms,
            max_flight_time_min=m.max_flight_time_min,
            max_route_km=m.max_route_km,
            max_wind_ms=m.max_wind_ms,
            comm_range_km=m.comm_range_km,
            height_min_m=m.height_min_m,
            height_max_m=m.height_max_m,
            compatible_cameras=list(m.compatible_cameras),
        )
        for m in FLEET_MODELS.values()
    ]
