"""Задача ссылается на конкретный парк БВС.

Парков стало несколько (0005), поэтому «парк по умолчанию» исчез: расчёт
обязан знать, чьи борта распределять, и строит точку взлёта по координатам
экземпляра. Колонка NOT NULL.

Уже существующим задачам парк проставляется, только если он в системе ровно
один — тогда ответ единственный и его не надо угадывать. При нескольких парках
выбор за человеком: миграция останавливается и говорит, что сделать. Выбрать
«первый попавшийся» значило бы молча привязать задачи к чужому региону.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('fleet_id', sa.UUID(), nullable=True))

    conn = op.get_bind()
    orphan_tasks = conn.execute(sa.text("SELECT count(*) FROM tasks WHERE fleet_id IS NULL")).scalar_one()
    if orphan_tasks:
        fleet_ids = conn.execute(sa.text("SELECT id FROM fleet_uploads")).scalars().all()
        if len(fleet_ids) == 1:
            conn.execute(
                sa.text("UPDATE tasks SET fleet_id = :fleet_id WHERE fleet_id IS NULL"),
                {"fleet_id": fleet_ids[0]},
            )
        else:
            raise RuntimeError(
                f"{orphan_tasks} задач без парка БВС, а парков в системе {len(fleet_ids)} — "
                "выбрать за вас нельзя. Проставьте парк вручную "
                "(UPDATE tasks SET fleet_id = '<id парка>' WHERE fleet_id IS NULL) "
                "и накатите миграцию снова, либо удалите старые задачи."
            )

    op.alter_column('tasks', 'fleet_id', nullable=False)
    op.create_foreign_key(
        op.f('fk_tasks_fleet_id_fleet_uploads'), 'tasks', 'fleet_uploads',
        ['fleet_id'], ['id'], ondelete='RESTRICT',
    )
    op.create_index('ix_tasks_fleet_id', 'tasks', ['fleet_id'])


def downgrade() -> None:
    op.drop_index('ix_tasks_fleet_id', table_name='tasks')
    op.drop_constraint(op.f('fk_tasks_fleet_id_fleet_uploads'), 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'fleet_id')
