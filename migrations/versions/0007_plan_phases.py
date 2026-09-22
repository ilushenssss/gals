"""Этапы вылета и подтверждение вопреки нарушениям.

``plan_sorties.phases`` — разбиение вылета на перелёт, галсы, переходы и
возврат: переходы теперь строятся в обход зон (сеточный A*), заметно
отличаются от прямой, и расписание обязано их показывать (ИНТ.ФТ.15).
Структуру задаёт расчёт и она будет меняться — поэтому jsonb, а не таблица.

``plans.confirmed_with_overrides`` — план подтверждён вопреки нарушениям,
потому что оператор пометил принятыми все нарушения последнего отчёта
(расширение ЭКС.ФТ.2 по запросу пользователя).

Существующим планам этапы не восстановить: они считались, когда переходов как
отдельных сущностей не было. Пустой список честнее выдуманного разбиения —
интерфейс просто не раскроет пункт расписания у старого плана.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'plan_sorties',
        sa.Column('phases', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
    )
    op.add_column(
        'plans',
        sa.Column('confirmed_with_overrides', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # server_default нужен только на время наката непустых таблиц; значение по
    # умолчанию задаёт приложение, иначе autogenerate вечно видит расхождение.
    op.alter_column('plan_sorties', 'phases', server_default=None)
    op.alter_column('plans', 'confirmed_with_overrides', server_default=None)


def downgrade() -> None:
    op.drop_column('plans', 'confirmed_with_overrides')
    op.drop_column('plan_sorties', 'phases')
