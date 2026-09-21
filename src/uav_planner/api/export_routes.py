"""HTTP-маршруты модуля «Подтверждение и экспорт» — ЭКС.ФТ.2-3, ЭКС.ФТ.6-9."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Response

from . import export_service, plan_service, safety_service
from .plan_models import PlanDetail
from .plan_service import PlanAlreadyConfirmedError, PlanNotReviewedError

router = APIRouter(prefix="/api", tags=["export"])

# v1: в системе нет модели пользователей/аутентификации (см. PlanDetail.confirmed_by) —
# «ФИО» подтверждающего вводится оператором свободным текстом в форме подтверждения.
DEFAULT_CONFIRMED_BY = "Оператор"


def _violations_overridden(plan_id: str) -> bool:
    """Расширение поверх ЭКС.ФТ.2 (по запросу пользователя): если план не
    прошел проверку, но оператор явно отметил принятыми ВСЕ нарушения
    ПОСЛЕДНЕГО отчета проверки безопасности для этого плана — подтверждение
    все равно разрешается. Проверяется здесь (а не в ``plan_service``), так
    как только этот модуль видит и ``plan_service``, и ``safety_service``."""
    try:
        report = safety_service.get_latest_report(plan_id)
    except KeyError:
        return False
    return report.status == "Есть нарушения" and report.violations_acknowledged


@router.post("/plans/{plan_id}/confirm", response_model=PlanDetail)
def confirm_plan(plan_id: str, confirmed_by: str = Form(DEFAULT_CONFIRMED_BY)) -> PlanDetail:
    override = _violations_overridden(plan_id)
    try:
        return plan_service.confirm_plan(
            plan_id, confirmed_by.strip() or DEFAULT_CONFIRMED_BY, override_violations=override,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="план не найден")
    except PlanAlreadyConfirmedError as exc:
        # ЭКС.ФТ.9: второй запрос на подтверждение — конфликт, а не тихий успех.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlanNotReviewedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _get_confirmed_plan(plan_id: str) -> PlanDetail:
    try:
        plan = plan_service.get_plan(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="план не найден")
    if plan.status not in ("Подтвержден", "Выгружен"):
        raise HTTPException(status_code=409, detail="экспорт доступен только для подтвержденного плана")
    return plan


@router.get("/plans/{plan_id}/export/kml/{uav_id}")
def export_kml(plan_id: str, uav_id: str) -> Response:
    plan = _get_confirmed_plan(plan_id)
    try:
        content = export_service.to_kml(plan, uav_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    plan_service.mark_exported(plan_id)
    return Response(
        content=content,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="{uav_id}_v{plan.version}.kml"'},
    )


@router.get("/plans/{plan_id}/export/geojson/{uav_id}")
def export_geojson(plan_id: str, uav_id: str) -> Response:
    plan = _get_confirmed_plan(plan_id)
    try:
        content = export_service.to_geojson(plan, uav_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    plan_service.mark_exported(plan_id)
    return Response(
        content=json.dumps(content, ensure_ascii=False, indent=2),
        media_type="application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="{uav_id}_v{plan.version}.geojson"'},
    )


@router.get("/plans/{plan_id}/export/zip")
def export_zip(plan_id: str) -> Response:
    plan = _get_confirmed_plan(plan_id)
    content = export_service.to_zip(plan)
    plan_service.mark_exported(plan_id)
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="plan_v{plan.version}_export.zip"'},
    )
