"""HTTP-маршруты модуля «Планирование» — ПЛН.ФТ.5-9."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException

from . import plan_service
from .plan_models import PlanDetail, PlanSummary
from .plan_service import PlanInfeasibleError

router = APIRouter(prefix="/api", tags=["plans"])


@router.post("/plans", response_model=PlanSummary)
def create_plan(task_id: str = Form(...)) -> PlanSummary:
    try:
        return plan_service.create_plan(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="задача не найдена")
    except PlanInfeasibleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/plans", response_model=list[PlanSummary])
def list_plans(task_id: str) -> list[PlanSummary]:
    return plan_service.list_plans(task_id)


@router.get("/plans/{plan_id}", response_model=PlanDetail)
def get_plan(plan_id: str) -> PlanDetail:
    try:
        return plan_service.get_plan(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="план не найден")
