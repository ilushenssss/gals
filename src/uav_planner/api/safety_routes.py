"""HTTP-маршруты модуля «Проверка безопасности» — БЕЗ.ФТ.1-6."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException

from . import safety_service
from .safety_models import SafetyReport
from .safety_service import SafetyCheckError

router = APIRouter(prefix="/api", tags=["safety"])


@router.post("/safety-checks", response_model=SafetyReport)
def run_safety_check(plan_id: str = Form(...)) -> SafetyReport:
    try:
        return safety_service.check_plan(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="план не найден")
    except SafetyCheckError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/safety-checks/recheck", response_model=SafetyReport)
def recheck_safety_check(plan_id: str = Form(...)) -> SafetyReport:
    """БЕЗ.ФТ.6 «Повторить проверку» — без автоматического пересчета."""
    try:
        return safety_service.recheck_plan(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="план не найден")
    except SafetyCheckError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/safety-checks/latest", response_model=SafetyReport)
def get_latest_safety_check(plan_id: str) -> SafetyReport:
    try:
        return safety_service.get_latest_report(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
