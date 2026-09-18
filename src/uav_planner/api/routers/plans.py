"""HTTP-маршруты модуля «Планирование» — ПЛН.ФТ.2-9.

``POST /api/plans`` устроен гибридно: расчет всегда уходит в очередь (ПЛН.ФТ.5),
но эндпоинт до ``wait_s`` секунд ждет его завершения и, если дождался, отдает
прежний ``200 PlanSummary`` — тот же байт в байт ответ, что был до появления
воркера. Не дождался — ``202`` с объектом работы и заголовком ``Location``.

Зачем так, а не «всегда 202»: сцены тестового и демонстрационного масштаба
считаются за миллисекунды, и фронт, которому для них пришлось бы городить
поллинг, стал бы заметно хуже без единой выгоды. В проде ``wait_s=0``, и
эндпоинт всегда отвечает 202.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Header, HTTPException, Response

from uav_planner.api.schemas.job import JobAccepted
from uav_planner.api.schemas.plan import PlanDetail, PlanSummary
from uav_planner.config import get_settings
from uav_planner.services import job_service, plan_service, task_service

router = APIRouter(prefix="/api", tags=["plans"])


@router.post("/plans", response_model=None)
def create_plan(
    response: Response,
    task_id: str = Form(...),
    wait_s: float | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PlanSummary | JobAccepted:
    # Существование задачи проверяется до постановки в очередь: «задача не
    # найдена» — это 404 запроса, а не работа, которая заведомо упадет.
    # Остальные условия ПЛН.ФТ.2 (статус обстановки, наличие критерия)
    # проверяет сам расчет и отдает их через error_code='infeasible' -> 422.
    try:
        task_service.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="задача не найдена")

    job, _created = job_service.submit("plan", task_id, idempotency_key=idempotency_key)
    settings = get_settings()
    job = job_service.wait_for(
        job.id, settings.plan_sync_wait_seconds if wait_s is None else wait_s
    )

    failure = job_service.result_error(job)
    if failure is not None:
        raise HTTPException(status_code=failure[0], detail=failure[1])

    if job.is_finished and job.result_plan_id:
        return plan_service.get_plan_summary(job.result_plan_id)

    response.status_code = 202
    response.headers["Location"] = f"/api/plan-jobs/{job.id}"
    return JobAccepted(job=job)


@router.get("/plans", response_model=list[PlanSummary])
def list_plans(task_id: str) -> list[PlanSummary]:
    return plan_service.list_plans(task_id)


@router.get("/plans/{plan_id}", response_model=PlanDetail)
def get_plan(plan_id: str) -> PlanDetail:
    try:
        return plan_service.get_plan(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="план не найден")
