"""HTTP-маршруты модуля «Парк БВС» — ПБС.ФТ.5-11."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from uav_planner.fleet import FLEET_MODELS

from uav_planner.services import fleet_service
from uav_planner.api.schemas.fleet import FleetDetail, FleetSummary, ModelSpecOut

router = APIRouter(prefix="/api", tags=["fleet"])


@router.post("/fleets", response_model=FleetSummary)
async def create_fleet(
    name: str = Form(...),
    location_name: str | None = Form(None),
    file: UploadFile = File(...),
) -> FleetSummary:
    raw = await file.read()
    try:
        return fleet_service.create_fleet(name=name, location_name=location_name, raw=raw)
    except fleet_service.FleetLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"не удалось разобрать файл парка: {exc}") from exc


@router.get("/fleets", response_model=list[FleetSummary])
def list_fleets() -> list[FleetSummary]:
    return fleet_service.list_fleets()


# Объявлен ДО /fleets/{fleet_id}: иначе «models» уедет в параметр пути.
@router.get("/fleets/models", response_model=list[ModelSpecOut])
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


@router.get("/fleets/{fleet_id}", response_model=FleetDetail)
def get_fleet(fleet_id: str) -> FleetDetail:
    try:
        return fleet_service.get_fleet(fleet_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="парк БВС не найден")
