"""Рекомендации по устранению нарушений в отчете проверки безопасности.

БЕЗ.ФТ.4 требует показывать при нарушении «предлагаемые варианты решения».
У каждого критерия отчета (``safety_checks``) появляется список строк
``recommendations``: для высоты — максимально допустимое GSD при потолке
150 м, для геозон — что сделал автопересчет с расширенным буфером и что
остается оператору. Старым отчетам рекомендаций взять неоткуда — пустой
список.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "safety_checks",
        sa.Column("recommendations", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("safety_checks", "recommendations")
