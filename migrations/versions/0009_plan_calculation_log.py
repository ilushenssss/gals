"""История расчета плана простым языком.

``plans.calculation_log`` — накопительный список фраз с подставленными
числами (что и как считалось, начиная с расчета галсов, почему выбран именно
этот кандидат «модель+камера»), см. ``plan_service.build_candidate`` и
``_pick_best_candidate``. Существующим планам историю не восстановить — они
считались до появления этого журнала — пустой список честнее выдуманного
текста.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'plans',
        sa.Column('calculation_log', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
    )
    op.alter_column('plans', 'calculation_log', server_default=None)


def downgrade() -> None:
    op.drop_column('plans', 'calculation_log')
