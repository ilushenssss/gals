"""HTTP-маршруты модуля «Проверка безопасности» — БЕЗ.ФТ.1-6.

``POST /api/safety-checks`` работает так же гибридно, как запуск расчета: цикл
«проверка → автоматический пересчет → проверка» (БЕЗ.ФТ.3) уходит в очередь,
эндпоинт до ``wait_s`` секунд ждет и, дождавшись, отдает прежний ``200
SafetyReport``. Именно перенос цикла в воркер делает наблюдаемым третий статус
БЕЗ.ФТ.5 «В процессе автоматического пересчета»: внутри одного HTTP-запроса
это состояние снаружи не видно в принципе.

``recheck`` в очередь не уходит — он по определению не пересчитывает план
(БЕЗ.ФТ.6) и выполняется за то же время, что и одна проверка.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Header, HTTPException, Response

from uav_planner.api.schemas.job import JobAccepted
from uav_planner.api.schemas.safety import SafetyReport
from uav_planner.config import get_settings
from uav_planner.services import job_service, plan_service, safety_service
from uav_planner.services.safety_service import SafetyCheckError

router = APIRouter(prefix="/api", tags=["safety"])


@router.post("/safety-checks", response_model=None)
def run_safety_check(
    response: Response,
    plan_id: str = Form(...),
    wait_s: float | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SafetyReport | JobAccepted:
    try:
        plan = plan_service.get_plan(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="план не найден")

    job, _created = job_service.submit(
        "safety", plan.task_id, plan_id=plan_id, idempotency_key=idempotency_key
    )
    settings = get_settings()
    job = job_service.wait_for(
        job.id, settings.plan_sync_wait_seconds if wait_s is None else wait_s
    )

    failure = job_service.result_error(job)
    if failure is not None:
        raise HTTPException(status_code=failure[0], detail=failure[1])

    if job.is_finished and job.result_report_id:
        return safety_service.get_report(job.result_report_id)

    response.status_code = 202
    response.headers["Location"] = f"/api/plan-jobs/{job.id}"
    return JobAccepted(job=job)


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


@router.post(
    "/safety-checks/{report_id}/violations/{violation_id}/ignore",
    response_model=SafetyReport,
)
def ignore_violation(report_id: str, violation_id: str, ignored: bool = Form(...)) -> SafetyReport:
    """Оператор принимает риск конкретного нарушения (или снимает отметку).

    Если так отмечены все нарушения отчёта, план можно подтвердить вопреки
    ЭКС.ФТ.2 — расширение по запросу пользователя.
    """
    try:
        return safety_service.set_violation_ignored(report_id, violation_id, ignored)
    except safety_service.ViolationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="отчет проверки не найден")


@router.post(
    "/safety-checks/{report_id}/violations/ignore-all",
    response_model=SafetyReport,
)
def ignore_all_violations(report_id: str, ignored: bool = Form(...)) -> SafetyReport:
    """Кнопка «Игнорировать все нарушения» — то же самое, что отметить
    вручную каждую галочку по очереди, одним действием (по запросу
    пользователя)."""
    try:
        return safety_service.set_all_violations_ignored(report_id, ignored)
    except safety_service.ViolationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="отчет проверки не найден")
