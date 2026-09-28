"""Параметры съемки на уровне вылета — для смешанного парка.

План смешанного парка содержит вылеты разных моделей БВС: у каждой своя
высота съемки, полоса захвата, крейсерская скорость и энергобюджет. Проверка
безопасности и выгрузка должны сверять вылет с его собственными параметрами,
а не с одним набором на весь план. У планов, рассчитанных раньше, колонки
пустые — для них по-прежнему действуют параметры плана.

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

_TEXT_COLUMNS = ("model_key", "camera_key")
_FLOAT_COLUMNS = ("height_m", "swath_m", "cruise_speed_mps", "budget_s")


def upgrade() -> None:
    for name in _TEXT_COLUMNS:
        op.add_column("plan_sorties", sa.Column(name, sa.Text(), nullable=True))
    for name in _FLOAT_COLUMNS:
        op.add_column("plan_sorties", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in reversed(_TEXT_COLUMNS + _FLOAT_COLUMNS):
        op.drop_column("plan_sorties", name)
