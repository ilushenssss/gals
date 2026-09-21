"""Снять схему OpenAPI прямо с объекта приложения.

Не с поднятого uvicorn: подъем сервера ради схемы — лишняя движущаяся часть в
CI, а результат тот же. Схема и сгенерированные из нее типы коммитятся, в CI
регенерация плюс ``git diff --exit-code`` ловит рассинхрон фронта с бэком.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "web/openapi.json")
    # Подключение к БД для снятия схемы не нужно и только мешает в CI.
    from uav_planner.config import Settings
    from uav_planner.api.app import create_app

    app = create_app(Settings(database_url=""))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"схема записана: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
