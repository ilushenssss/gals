"""Структурное логирование и корреляция по job_id/task_id.

Проверяется не текст сообщений, а то, ради чего журнал и заводился: что запись
машиночитаема, что идентификаторы из контекста в нее попадают и что контекст не
протекает за границу блока.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from uav_planner.config import Settings
from uav_planner.logging_setup import (
    JsonFormatter,
    TextFormatter,
    configure_logging,
    get_log_context,
    log_context,
)


def _render(formatter: logging.Formatter, level: int = logging.INFO, **extra) -> str:
    """Провести запись через тот же конвейер, что и настоящий обработчик."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    from uav_planner.logging_setup import _ContextFilter

    handler.addFilter(_ContextFilter())
    logger = logging.getLogger("uav_planner.test.render")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.log(level, "расчет начат", extra=extra or None)
    return stream.getvalue().strip()


def test_json_record_is_machine_readable_and_keeps_russian_text():
    line = _render(JsonFormatter())
    record = json.loads(line)
    assert record["level"] == "INFO"
    assert record["message"] == "расчет начат"
    assert record["logger"] == "uav_planner.test.render"
    assert record["ts"].endswith("Z")
    # ensure_ascii=False: сообщения русские, экранированный юникод не читается.
    assert "расчет начат" in line


def test_context_fields_land_in_the_record():
    with log_context(job_id="job-1", task_id="task-1"):
        record = json.loads(_render(JsonFormatter()))
    assert record["job_id"] == "job-1"
    assert record["task_id"] == "task-1"


def test_extra_fields_land_in_the_record():
    record = json.loads(_render(JsonFormatter(), job_status="Завершен", duration_s=1.5))
    assert record["job_status"] == "Завершен"
    assert record["duration_s"] == 1.5


def test_context_does_not_leak_past_the_block():
    with log_context(job_id="job-1"):
        with log_context(task_id="task-1"):
            assert get_log_context() == {"job_id": "job-1", "task_id": "task-1"}
        assert get_log_context() == {"job_id": "job-1"}
    assert get_log_context() == {}


def test_none_values_are_not_written_to_the_context():
    """Вызывающему не нужно ветвиться на то, знает ли он уже идентификатор."""
    with log_context(job_id="job-1", plan_id=None):
        assert get_log_context() == {"job_id": "job-1"}


def test_text_format_keeps_context_readable():
    with log_context(job_id="job-1"):
        line = _render(TextFormatter())
    assert "расчет начат" in line
    assert "job_id=job-1" in line


@pytest.mark.parametrize(
    ("env", "log_format", "expected_json"),
    [("prod", "auto", True), ("dev", "auto", False), ("dev", "json", True), ("prod", "text", False)],
)
def test_format_is_chosen_by_environment(env, log_format, expected_json):
    assert Settings(env=env, log_format=log_format).log_as_json is expected_json


def test_configure_logging_replaces_foreign_handlers():
    """uvicorn и celery ставят свои обработчики — иначе в одном потоке живут
    три разных формата сразу."""
    foreign = logging.getLogger("uvicorn.error")
    foreign.handlers = [logging.StreamHandler()]
    root_handlers = logging.getLogger().handlers[:]
    try:
        configure_logging(Settings(env="prod", log_level="INFO"), force=True)
        assert foreign.handlers == []
        assert foreign.propagate is True
        assert len(logging.getLogger().handlers) == 1
        assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
    finally:
        logging.getLogger().handlers = root_handlers


def test_response_carries_the_request_id(client):
    """По идентификатору в ответе находится строка в журнале сервиса."""
    response = client.get("/api/health")
    assert response.headers["x-request-id"]


def test_request_id_from_the_client_is_kept(client):
    """Свой идентификатор от reverse proxy не перебивается: сквозная
    трассировка запроса важнее, чем уникальность нашей генерации."""
    response = client.get("/api/health", headers={"X-Request-Id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"
