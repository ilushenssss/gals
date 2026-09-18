"""Расширения PostgreSQL: PostGIS и pgcrypto.

Первая миграция не создает ни одной таблицы — только включает расширения.
Таблицы появляются в 0002+ по мере перевода модулей на БД (см. план, шаг 3).

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    # Расширения намеренно не удаляем: на них может опираться что-то еще в БД,
    # а DROP EXTENSION postgis уронил бы все гео-колонки разом.
    pass
