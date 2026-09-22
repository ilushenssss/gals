"""Нарушения проверки безопасности стали объектами, а не строками.

У нарушения появились стабильный в пределах отчёта идентификатор, координата
«опасного момента» для маркера на карте (БЕЗ.ФТ.4, ИНТ.ФТ.14) и отметка
«оператор принял риск». Тип колонки не меняется — jsonb и был jsonb, — но
содержимое существующих отчётов надо перевести, иначе чтение падает на
первом же старом отчёте.

Координаты старым нарушениям взять неоткуда: их считали, когда проверки
возвращали только текст. Оставляем пустыми — маркер просто не появится на
карте, а текст нарушения сохраняется полностью. Выдумывать точку хуже, чем
её не иметь: маркер в неверном месте направит оператора не туда.

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Идентификатор собирается так же, как его строит safety_service:
    # «{имя проверки}__{порядковый номер}».
    op.get_bind().execute(sa.text("""
        UPDATE safety_checks SET violations = (
            SELECT coalesce(jsonb_agg(
                jsonb_build_object(
                    'id', name || '__' || (ordinality - 1)::text,
                    'message', value #>> '{}',
                    'lat', NULL,
                    'lon', NULL,
                    'ignored', false
                ) ORDER BY ordinality
            ), '[]'::jsonb)
            FROM jsonb_array_elements(violations) WITH ORDINALITY
        )
        WHERE jsonb_typeof(violations->0) = 'string'
    """))


def downgrade() -> None:
    op.get_bind().execute(sa.text("""
        UPDATE safety_checks SET violations = (
            SELECT coalesce(jsonb_agg(value->>'message' ORDER BY ordinality), '[]'::jsonb)
            FROM jsonb_array_elements(violations) WITH ORDINALITY
        )
        WHERE jsonb_typeof(violations->0) = 'object'
    """))
