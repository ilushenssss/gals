"""Парков много: имя и локация у парка, координаты у экземпляра.

Расширение ПБС.ФТ.11 по запросу пользователя: прежде парк был один и
повторная загрузка заменяла его целиком (частичный уникальный индекс по
``is_current``). Теперь парк — самостоятельная именованная сущность со своей
локацией, а задача ссылается на конкретный парк.

Локация парка вычисляется из координат экземпляров при загрузке, поэтому она
NOT NULL. Для уже существующих загрузок вывести её НЕ ИЗ ЧЕГО: колонка
``fleet_instances.location_lat`` появляется этой же миграцией и у всех старых
строк пуста. Проставить вместо неё ноль означало бы отправить парк в Гвинейский
залив и получить молча неверный расчёт, поэтому миграция в таком случае
останавливается с объяснением, что делать. Имя, в отличие от координат,
выводится честно — из даты загрузки.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Координаты экземпляров — первыми: из них выводится локация парка.
    op.add_column('fleet_instances', sa.Column('location_lat', sa.Float(), nullable=True))
    op.add_column('fleet_instances', sa.Column('location_lon', sa.Float(), nullable=True))

    op.add_column('fleet_uploads', sa.Column('name', sa.Text(), nullable=True))
    op.add_column('fleet_uploads', sa.Column('location_lat', sa.Float(), nullable=True))
    op.add_column('fleet_uploads', sa.Column('location_lon', sa.Float(), nullable=True))
    op.add_column('fleet_uploads', sa.Column('location_name', sa.Text(), nullable=True))

    conn = op.get_bind()

    # Имя: выводится из даты загрузки — прежде парк был безымянным.
    conn.execute(sa.text(
        "UPDATE fleet_uploads "
        "SET name = 'Парк от ' || to_char(uploaded_at AT TIME ZONE 'UTC', 'DD.MM.YYYY HH24:MI') "
        "WHERE name IS NULL"
    ))

    # Локация: среднее по экземплярам, у которых координаты есть.
    conn.execute(sa.text(
        "UPDATE fleet_uploads u SET location_lat = a.lat, location_lon = a.lon "
        "FROM (SELECT upload_id, avg(location_lat) AS lat, avg(location_lon) AS lon "
        "      FROM fleet_instances "
        "      WHERE location_lat IS NOT NULL AND location_lon IS NOT NULL "
        "      GROUP BY upload_id) a "
        "WHERE u.id = a.upload_id AND u.location_lat IS NULL"
    ))

    orphans = conn.execute(sa.text(
        "SELECT count(*) FROM fleet_uploads WHERE location_lat IS NULL OR location_lon IS NULL"
    )).scalar_one()
    if orphans:
        raise RuntimeError(
            f"невозможно определить локацию для {orphans} загруженных парков БВС: "
            "ни у одного экземпляра не указаны координаты (location_lat/location_lon). "
            "Расчёт плана теперь строит точку взлёта по координатам экземпляра, поэтому "
            "парк без локации непригоден. Перезалейте файлы парков с колонками "
            "location_lat/location_lon или удалите старые загрузки "
            "(DELETE FROM fleet_uploads) и накатите миграцию снова."
        )

    op.alter_column('fleet_uploads', 'name', nullable=False)
    op.alter_column('fleet_uploads', 'location_lat', nullable=False)
    op.alter_column('fleet_uploads', 'location_lon', nullable=False)

    # «Текущего» парка больше нет — задача называет свой парк явно.
    op.drop_index('uq_fleet_uploads_is_current', table_name='fleet_uploads', postgresql_where='is_current')
    op.drop_column('fleet_uploads', 'is_current')


def downgrade() -> None:
    op.add_column('fleet_uploads', sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('fleet_uploads', 'is_current', server_default=None)
    # Текущим назначается последний загруженный — иначе частичный уникальный
    # индекс не даст пометить сразу несколько.
    op.get_bind().execute(sa.text(
        "UPDATE fleet_uploads SET is_current = true WHERE id = "
        "(SELECT id FROM fleet_uploads ORDER BY uploaded_at DESC LIMIT 1)"
    ))
    op.create_index('uq_fleet_uploads_is_current', 'fleet_uploads', ['is_current'], unique=True, postgresql_where='is_current')

    op.drop_column('fleet_uploads', 'location_name')
    op.drop_column('fleet_uploads', 'location_lon')
    op.drop_column('fleet_uploads', 'location_lat')
    op.drop_column('fleet_uploads', 'name')
    op.drop_column('fleet_instances', 'location_lon')
    op.drop_column('fleet_instances', 'location_lat')
