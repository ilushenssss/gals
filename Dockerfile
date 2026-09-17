FROM python:3.12-slim

WORKDIR /app

# Слой зависимостей отдельно от кода — пересборка кода не тянет переустановку пакетов.
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "uav_planner.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
