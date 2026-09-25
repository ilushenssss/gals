# Один образ на четыре роли: api, worker, beat и одноразовый migrate —
# различаются только командой запуска (см. docker-compose.yml).

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Слой зависимостей отдельно от кода: правка кода не тянет переустановку пакетов.
COPY pyproject.toml ./
RUN mkdir -p src/uav_planner && touch src/uav_planner/__init__.py \
    && pip install --no-cache-dir . \
    && pip uninstall -y uav-planner

COPY src ./src
RUN pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "uav_planner.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
