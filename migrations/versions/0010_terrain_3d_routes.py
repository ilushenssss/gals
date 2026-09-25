"""Маршруты вылета стали 3D — высота над рельефом (раздел 3.4 статьи,
Ответы_экспертов_Геоскан.pdf вопрос 13).

``plan_sorties.route_geom``/``survey_tracks_geom`` хранили только (lon, lat);
теперь третья координата — абсолютная высота Z (рельеф + целевая высота над
поверхностью, либо плоский фолбэк, если рельеф выключен/недоступен, см.
``plan_service._apply_terrain_profile``) — обе колонки всегда 3D, ни одна
строка не смешивает 2D и 3D. Существующим (2D) маршрутам взять Z неоткуда —
``ST_Force3D`` подставляет 0 м, что честнее, чем NULL или выдуманная высота:
это старые планы, посчитанные до рельефа, и пересчет создаст новую версию
с настоящим профилем.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(sa.text("""
        ALTER TABLE plan_sorties
        ALTER COLUMN route_geom TYPE geometry(LineStringZ, 4326)
        USING ST_Force3D(route_geom)
    """))
    op.get_bind().execute(sa.text("""
        ALTER TABLE plan_sorties
        ALTER COLUMN survey_tracks_geom TYPE geometry(MultiLineStringZ, 4326)
        USING ST_Force3D(survey_tracks_geom)
    """))


def downgrade() -> None:
    op.get_bind().execute(sa.text("""
        ALTER TABLE plan_sorties
        ALTER COLUMN route_geom TYPE geometry(LineString, 4326)
        USING ST_Force2D(route_geom)
    """))
    op.get_bind().execute(sa.text("""
        ALTER TABLE plan_sorties
        ALTER COLUMN survey_tracks_geom TYPE geometry(MultiLineString, 4326)
        USING ST_Force2D(survey_tracks_geom)
    """))
