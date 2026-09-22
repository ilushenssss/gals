"""HTTP-маршруты фонового расчета — ПЛН.ФТ.5 (прогресс и отмена).

``GET /api/plan-jobs?task_id=`` не вспомогательный, а обязательный: получасовой
расчет должен переживать перезагрузку вкладки. Открывая экран задачи, SPA
спрашивает работы по ней и переподключает индикатор прогресса; без этого
«фоновый расчет» ломается на первом F5.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from uav_planner.api.schemas.job import JobInfo
from uav_planner.services import job_service

router = APIRouter(prefix="/api", tags=["plan-jobs"])


@router.get("/plan-jobs", response_model=list[JobInfo])
def list_plan_jobs(task_id: str) -> list[JobInfo]:
    return job_service.list_jobs(task_id)


@router.get("/plan-jobs/{job_id}", response_model=JobInfo)
def get_plan_job(job_id: str) -> JobInfo:
    try:
        return job_service.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="расчет не найден")


@router.post("/plan-jobs/{job_id}/cancel", response_model=JobInfo)
def cancel_plan_job(job_id: str) -> JobInfo:
    """Отмена расчета. Идемпотентна: отмена завершенной работы — не ошибка,
    оператор мог нажать кнопку ровно в момент завершения."""
    try:
        return job_service.cancel(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="расчет не найден")
