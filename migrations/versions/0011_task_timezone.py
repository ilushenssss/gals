"""Часовой пояс окна работ задачи.

Окно работ (``window_start``/``window_end``) раньше сравнивалось с часами
UTC, хотя интерфейс показывает время в местном поясе оператора: «09:00–18:00»
в Москве фактически означало 12:00–21:00 МСК. Теперь окно — местное время в
поясе ``tasks.timezone`` (IANA, например «Europe/Moscow»). Существующим
задачам пояс взять неоткуда — ``NULL`` означает прежнее поведение (UTC), а
новый расчет по таким задачам не меняется, пока оператор не сохранит задачу
заново из интерфейса (он подставит пояс браузера).

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("timezone", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "timezone")
