"""Тесты очереди и фонового расчета — ПЛН.ФТ.3, ПЛН.ФТ.5, БЕЗ.ФТ.3/ФТ.5.

Celery работает в eager-режиме (см. ``conftest.eager_celery``): задача
выполняется в вызывающем потоке, поэтому к возврату из ``POST /api/plans``
работа уже завершена. Промежуточные состояния (отмена на границе стадии,
прерывание расчета рестартом) воспроизводятся не гонкой, а явной подготовкой
строки ``plan_jobs`` — так тест детерминирован.
"""

import io
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from uav_planner.jobs.progress import JobCancelled, ProgressReporter
from uav_planner.models.job import PlanJob


def square_coords(x0, y0, w, h):
    return [[[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h], [x0, y0]]]


def _upload_environment(client, no_fly=False):
    features = [
        {
            "type": "Feature",
            "properties": {"layer": "airspace", "h_min": 0, "h_max": 300},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.55, 55.70, 0.10, 0.01)},
        },
        {
            "type": "Feature",
            "properties": {"layer": "launch_site", "name": "ВПП-1"},
            "geometry": {"type": "Point", "coordinates": [37.56, 55.705]},
        },
    ]
    if no_fly:
        features.append({
            "type": "Feature",
            "properties": {"layer": "no_fly", "safety_buffer_m": 0},
            "geometry": {"type": "Polygon", "coordinates": square_coords(37.581, 55.7025, 0.003, 0.002)},
        })
    data = json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")
    resp = client.post(
        "/api/environments",
        data={"name": "Обстановка"},
        files={"file": ("scene.geojson", io.BytesIO(data), "application/json")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _upload_fleet(client, n=1, model="geoscan-gemini"):
    records = [{"inventory_number": f"{model}-{i}", "model": model, "status": "Готов"} for i in range(n)]
    data = json.dumps(records).encode("utf-8")
    assert client.post(
        "/api/fleet", files={"file": ("fleet.json", io.BytesIO(data), "application/json")}
    ).status_code == 200


def _create_task(client, env_id, **overrides):
    form = {
        "name": "Задача 1",
        "environment_id": env_id,
        "survey_type": "RGB",
        "gsd_cm": "3.0",
        "work_date": "2026-06-15",
        "criterion_mode": "Время",
    }
    form.update(overrides)
    area = {"type": "Polygon", "coordinates": square_coords(37.58, 55.702, 0.005, 0.004)}
    resp = client.post(
        "/api/tasks",
        data=form,
        files={"area_file": ("area.geojson", io.BytesIO(json.dumps(area).encode("utf-8")), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class _SlowQueue:
    """Подменяет отправку задачи в очередь на «сообщение принято, но не взято».

    Так проверяется ветка 202 гибридного ожидания, которую eager-режим иначе
    никогда не покажет: он выполняет задачу внутри ``delay()``.
    """

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch

    def enable(self):
        from uav_planner.jobs import tasks

        class _Accepted:
            id = None

        for task in (tasks.run_plan_job, tasks.run_safety_job):
            self._monkeypatch.setattr(task, "delay", lambda *a, **kw: _Accepted(), raising=True)


@pytest.fixture
def slow_queue(monkeypatch):
    return _SlowQueue(monkeypatch)


def _ready_task(client, n_fleet=1, no_fly=False):
    env_id = _upload_environment(client, no_fly=no_fly)
    _upload_fleet(client, n=n_fleet)
    return _create_task(client, env_id)


# --- запуск расчета через очередь -------------------------------------------


def test_plan_run_creates_job_row(client):
    """Даже успевший синхронно расчет проходит через очередь — работа видна."""
    task_id = _ready_task(client)
    plan = client.post("/api/plans", data={"task_id": task_id}).json()

    jobs = client.get("/api/plan-jobs", params={"task_id": task_id}).json()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["kind"] == "plan"
    assert job["status"] == "Завершен"
    assert job["progress"] == 100
    assert job["result_plan_id"] == plan["id"]
    assert job["started_at"] is not None and job["finished_at"] is not None


def test_plan_run_returns_202_when_the_run_has_not_finished(client, slow_queue):
    """Режим прода (wait_s=0): ответ 202, заголовок Location и работа в очереди.

    Eager-режим завершает задачу внутри ``submit``, поэтому ветку «не успел»
    воспроизводит фикстура ``slow_queue``: она не выполняет задачу, как если бы
    воркер еще не взял ее из очереди.
    """
    task_id = _ready_task(client)
    slow_queue.enable()
    resp = client.post("/api/plans", data={"task_id": task_id, "wait_s": "0"})

    assert resp.status_code == 202
    job = resp.json()["job"]
    assert resp.headers["Location"] == f"/api/plan-jobs/{job['id']}"
    assert job["status"] == "В очереди"
    assert job["result_plan_id"] is None

    polled = client.get(f"/api/plan-jobs/{job['id']}").json()
    assert polled["id"] == job["id"]
    assert polled["status"] == "В очереди"


def test_plan_run_with_zero_wait_returns_200_if_the_run_was_instant(client):
    """Сцена тестового масштаба считается быстрее опроса — тогда прежний 200."""
    task_id = _ready_task(client)
    resp = client.post("/api/plans", data={"task_id": task_id, "wait_s": "0"})
    assert resp.status_code == 200
    assert resp.json()["version"] == 1


def test_infeasible_task_fails_the_job_and_returns_422(client):
    """ПЛН.ФТ.10 через очередь: невыполнимость — исход работы, а не сбой."""
    env_id = _upload_environment(client)  # парк не загружен
    task_id = _create_task(client, env_id)

    resp = client.post("/api/plans", data={"task_id": task_id})
    assert resp.status_code == 422
    assert "парк" in resp.json()["detail"]

    job = client.get("/api/plan-jobs", params={"task_id": task_id}).json()[0]
    assert job["status"] == "Ошибка"
    assert job["error_code"] == "infeasible"
    assert job["result_plan_id"] is None


def test_unknown_job_returns_404(client):
    assert client.get(f"/api/plan-jobs/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/plan-jobs/не-uuid").status_code == 404


# --- идемпотентность --------------------------------------------------------


def test_same_idempotency_key_reuses_the_job(client):
    """Ретрай сети не должен превращаться во вторую версию плана (ПЛН.ФТ.4)."""
    task_id = _ready_task(client)
    headers = {"Idempotency-Key": "retry-1"}

    first = client.post("/api/plans", data={"task_id": task_id}, headers=headers).json()
    second = client.post("/api/plans", data={"task_id": task_id}, headers=headers).json()

    assert first["id"] == second["id"]
    assert len(client.get("/api/plan-jobs", params={"task_id": task_id}).json()) == 1
    assert len(client.get("/api/plans", params={"task_id": task_id}).json()) == 1


def test_repeated_calculation_without_key_creates_new_version(client):
    """А осознанный повторный расчет — наоборот, обязан дать новую версию."""
    task_id = _ready_task(client)
    client.post("/api/plans", data={"task_id": task_id})
    client.post("/api/plans", data={"task_id": task_id})

    assert len(client.get("/api/plan-jobs", params={"task_id": task_id}).json()) == 2
    assert [p["version"] for p in client.get("/api/plans", params={"task_id": task_id}).json()] == [2, 1]


def test_second_submit_while_active_returns_the_same_job(client, db):
    """Двойной клик по «Рассчитать»: одна активная работа на задачу."""
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    first, created = job_service.submit("plan", task_id)
    assert created

    # Работа в eager-режиме уже завершилась — возвращаем ее в «Выполняется»,
    # чтобы проверить именно защиту от второй активной работы.
    db.execute(
        update(PlanJob).where(PlanJob.id == uuid.UUID(first.id)).values(
            status="Выполняется", finished_at=None, heartbeat_at=datetime.now(timezone.utc)
        )
    )
    second, created_again = job_service.submit("plan", task_id)
    assert not created_again
    assert second.id == first.id


# --- отмена -----------------------------------------------------------------


def test_cancel_stops_the_run_at_a_stage_boundary(client, db):
    """ПЛН.ФТ.5: кооперативный флаг снимает расчет на границе стадии."""
    from uav_planner.jobs import tasks
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]
    # Работа уже отработала — ставим новую и просим отменить ее до запуска.
    job2 = job_service.submit("plan", task_id, idempotency_key="отмена")[0]
    db.execute(
        update(PlanJob).where(PlanJob.id == uuid.UUID(job2.id)).values(
            status="В очереди", finished_at=None, cancel_requested=True, result_plan_id=None
        )
    )

    tasks.run_plan_job(job2.id)

    finished = client.get(f"/api/plan-jobs/{job2.id}").json()
    assert job2.id != job.id
    assert finished["status"] == "Отменен"
    assert finished["error_code"] == "cancelled"
    assert finished["result_plan_id"] is None


def test_cancel_endpoint_is_idempotent_on_finished_job(client):
    """Кнопку могли нажать ровно в момент завершения — это не ошибка."""
    task_id = _ready_task(client)
    client.post("/api/plans", data={"task_id": task_id})
    job = client.get("/api/plan-jobs", params={"task_id": task_id}).json()[0]

    resp = client.post(f"/api/plan-jobs/{job['id']}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "Завершен"


def test_cancel_marks_queued_job_cancelled(client, db):
    """Работу, до которой воркер не дошел, отмена снимает сразу."""
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]
    db.execute(
        update(PlanJob).where(PlanJob.id == uuid.UUID(job.id)).values(
            status="В очереди", finished_at=None, cancel_requested=False
        )
    )

    cancelled = client.post(f"/api/plan-jobs/{job.id}/cancel").json()
    # В eager-режиме брокера нет, revoke невозможен — работа остается в
    # очереди с поднятым флагом, и снимет ее воркер на первой же стадии.
    assert cancelled["cancel_requested"] is True


# --- прерывание перезапуском (janitor) --------------------------------------


def _pretend_running(db, job_id: str, heartbeat_age_s: float | None) -> None:
    """Привести строку работы в состояние «воркер взял и молчит».

    ``heartbeat_age_s=None`` — heartbeat'а нет вовсе (воркер умер сразу после
    взятия работы), число — сколько секунд назад он был.
    """
    heartbeat = (
        None if heartbeat_age_s is None
        else datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_s)
    )
    db.execute(
        update(PlanJob).where(PlanJob.id == uuid.UUID(job_id)).values(
            status="Выполняется", finished_at=None, heartbeat_at=heartbeat,
        )
    )


def test_janitor_marks_a_job_without_heartbeat_as_interrupted(client, db):
    """Рестарт контейнера не должен оставлять вечное «Выполняется» (и не 404)."""
    from uav_planner.jobs.tasks import sweep_stale_jobs
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]
    _pretend_running(db, job.id, heartbeat_age_s=3600)

    assert sweep_stale_jobs() == 1

    polled = client.get(f"/api/plan-jobs/{job.id}").json()
    assert polled["status"] == "Ошибка"
    assert polled["error_code"] == "interrupted"
    assert "перезапуск" in polled["error"]


def test_janitor_ignores_a_job_with_a_fresh_heartbeat(client, db):
    """Идущий расчет не должен добиваться уборкой — он жив и пишет прогресс."""
    from uav_planner.jobs.tasks import sweep_stale_jobs
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]
    _pretend_running(db, job.id, heartbeat_age_s=1)

    assert sweep_stale_jobs() == 0
    assert client.get(f"/api/plan-jobs/{job.id}").json()["status"] == "Выполняется"


def test_janitor_leaves_queued_jobs_alone(client, db):
    """«В очереди» под сторожа не попадает: очередь переживает рестарт Redis,
    и долгое ожидание в ней — норма, а не смерть воркера."""
    from uav_planner.jobs.tasks import sweep_stale_jobs
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]
    db.execute(
        update(PlanJob).where(PlanJob.id == uuid.UUID(job.id)).values(
            status="В очереди", finished_at=None, heartbeat_at=None,
        )
    )

    assert sweep_stale_jobs() == 0
    assert client.get(f"/api/plan-jobs/{job.id}").json()["status"] == "В очереди"


def test_reading_a_stale_job_does_not_fix_it_by_itself(client, db):
    """Сторож переехал из пути чтения в janitor: опрос статуса больше ничего
    не чинит — иначе исход работы зависел бы от того, заглянет ли кто-нибудь."""
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]
    _pretend_running(db, job.id, heartbeat_age_s=3600)

    assert client.get(f"/api/plan-jobs/{job.id}").json()["status"] == "Выполняется"
    assert client.get(f"/api/plan-jobs?task_id={task_id}").json()[0]["status"] == "Выполняется"


# --- прогресс ---------------------------------------------------------------


def test_progress_is_visible_outside_the_transaction_of_the_run(client, db):
    """Прогресс пишется своей короткой транзакцией и коммитится сразу."""
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]

    reporter = ProgressReporter(job.id)
    reporter.stage("tracks", 42)

    assert client.get(f"/api/plan-jobs/{job.id}").json()["progress"] == 42


def test_progress_reporter_raises_on_cancel_request(client, db):
    task_id = _ready_task(client)
    from uav_planner.services import job_service

    job = job_service.submit("plan", task_id)[0]
    db.execute(
        update(PlanJob).where(PlanJob.id == uuid.UUID(job.id)).values(cancel_requested=True)
    )

    with pytest.raises(JobCancelled):
        ProgressReporter(job.id).stage("working_area")


def test_progress_reporter_without_job_is_inert(client):
    """Синхронный путь зовет сервисы без работы — репортер обязан молчать."""
    reporter = ProgressReporter(None)
    reporter.stage("load")
    reporter.heartbeat()
    reporter.check_cancelled()  # не должно ни писать, ни падать


# --- проверка безопасности как работа ---------------------------------------


def test_safety_check_runs_as_a_job(client):
    """БЕЗ.ФТ.3: цикл автопересчета живет в воркере, а не в HTTP-запросе."""
    task_id = _ready_task(client, n_fleet=2, no_fly=True)
    plan = client.post("/api/plans", data={"task_id": task_id}).json()

    report = client.post("/api/safety-checks", data={"plan_id": plan["id"]}).json()
    assert report["auto_recalc_count"] == 3

    jobs = client.get("/api/plan-jobs", params={"task_id": task_id}).json()
    safety_jobs = [j for j in jobs if j["kind"] == "safety"]
    assert len(safety_jobs) == 1
    job = safety_jobs[0]
    assert job["status"] == "Завершен"
    assert job["plan_id"] == plan["id"]  # что просили проверить
    assert job["result_report_id"] == report["id"]
    assert job["result_plan_id"] == report["plan_id"]  # последняя версия после пересчета
    assert job["auto_recalc_count"] == 3


def test_safety_check_returns_202_when_the_check_has_not_finished(client, slow_queue):
    task_id = _ready_task(client)
    plan = client.post("/api/plans", data={"task_id": task_id}).json()
    slow_queue.enable()  # план считаем как обычно, проверку — «не успевшей»

    resp = client.post("/api/safety-checks", data={"plan_id": plan["id"], "wait_s": "0"})
    assert resp.status_code == 202
    job = resp.json()["job"]
    assert job["kind"] == "safety"
    assert job["plan_id"] == plan["id"]
    assert resp.headers["Location"] == f"/api/plan-jobs/{job['id']}"


def test_safety_check_for_unknown_plan_returns_404_without_a_job(client):
    assert client.post("/api/safety-checks", data={"plan_id": "нет-такого"}).status_code == 404


# --- исходы, которые нельзя воспроизвести живой сценой ----------------------
#
# Текущее расчетное ядро — быстрые эвристики: сцена вчетверо крупнее ориентира
# ТЗ (100 км²/10 БВС) считается за десятые доли секунды, поэтому ни лимит
# времени, ни внутренний сбой живым запуском не вызвать. Проверяем их подменой
# самого расчета — каркас обработки исхода при этом настоящий.


def _run_plan_job_with(monkeypatch, client, exc):
    from uav_planner.jobs import tasks
    from uav_planner.services import job_service, plan_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id, idempotency_key="исход")[0]
    db_reset = update(PlanJob).where(PlanJob.id == uuid.UUID(job.id)).values(
        status="В очереди", finished_at=None, result_plan_id=None, cancel_requested=False
    )
    from uav_planner.db.session import current_session

    current_session().execute(db_reset)

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(plan_service, "create_plan", boom)
    tasks.run_plan_job(job.id)
    return client.get(f"/api/plan-jobs/{job.id}").json()


def test_soft_time_limit_stops_the_job_with_the_required_status(client, monkeypatch):
    """ПЛН.ФТ.3: по достижении лимита — свой статус, а не «Ошибка»."""
    from celery.exceptions import SoftTimeLimitExceeded

    job = _run_plan_job_with(monkeypatch, client, SoftTimeLimitExceeded())
    assert job["status"] == "Остановлен по лимиту времени"
    assert job["error_code"] == "timeout"
    assert "лимит" in job["error"]


def test_unexpected_failure_lands_in_the_job_row(client, monkeypatch):
    """Сбой обязан стать наблюдаемым исходом работы, а не молчаливой потерей."""
    job = _run_plan_job_with(monkeypatch, client, RuntimeError("деление на ноль в проекции"))
    assert job["status"] == "Ошибка"
    assert job["error_code"] == "internal"
    assert "проекции" in job["error"]


def test_cancelled_message_delivered_after_restart_is_refused(client, db):
    """revoke — широковещательное сообщение и не доходит до выключенного
    воркера: отмененная работа, доставленная после его запуска, обязана быть
    отсеяна по строке, а не рассчитана заново."""
    from uav_planner.jobs import tasks
    from uav_planner.services import job_service

    task_id = _ready_task(client)
    job = job_service.submit("plan", task_id)[0]
    db.execute(
        update(PlanJob).where(PlanJob.id == uuid.UUID(job.id)).values(
            status="Отменен", started_at=None, finished_at=datetime.now(timezone.utc),
            result_plan_id=None, cancel_requested=True,
        )
    )

    tasks.run_plan_job(job.id)

    refused = client.get(f"/api/plan-jobs/{job.id}").json()
    assert refused["status"] == "Отменен"
    assert refused["started_at"] is None
    assert refused["result_plan_id"] is None
