"""socios: credenciales y padrón elegible por propuesta

Solo AGREGA dos tablas que faltaban en 0002 (credenciales_socios y
propuesta_socios_elegibles). No modifica tablas existentes ni borra datos.

ATENCIÓN: downgrade() elimina estas dos tablas y sus datos. Ejecutarlo solo después
de respaldar la BD.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('credenciales_socios',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('socio_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('nombre', sa.String(length=80), nullable=False),
    sa.Column('activo', sa.Boolean(), nullable=False),
    sa.Column('creado_en', sa.String(), nullable=False),
    sa.Column('expira_en', sa.String(), nullable=True),
    sa.Column('revocado_en', sa.String(), nullable=True),
    sa.Column('ultimo_uso_en', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['socio_id'], ['socios.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('credenciales_socios', schema=None) as batch_op:
        batch_op.create_index('ix_credencial_socio_activa', ['socio_id', 'activo'], unique=False)
        batch_op.create_index(batch_op.f('ix_credenciales_socios_socio_id'), ['socio_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_credenciales_socios_token_hash'), ['token_hash'], unique=True)

    op.create_table('propuesta_socios_elegibles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('propuesta_id', sa.Integer(), nullable=False),
    sa.Column('socio_id', sa.Integer(), nullable=False),
    sa.Column('public_key_snapshot', sa.String(length=56), nullable=False),
    sa.Column('creado_en', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['propuesta_id'], ['propuestas.id'], ),
    sa.ForeignKeyConstraint(['socio_id'], ['socios.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('propuesta_id', 'socio_id', name='uq_propuesta_socio_elegible')
    )
    with op.batch_alter_table('propuesta_socios_elegibles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_propuesta_socios_elegibles_propuesta_id'), ['propuesta_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_propuesta_socios_elegibles_socio_id'), ['socio_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('propuesta_socios_elegibles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_propuesta_socios_elegibles_socio_id'))
        batch_op.drop_index(batch_op.f('ix_propuesta_socios_elegibles_propuesta_id'))
    op.drop_table('propuesta_socios_elegibles')

    with op.batch_alter_table('credenciales_socios', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_credenciales_socios_token_hash'))
        batch_op.drop_index(batch_op.f('ix_credenciales_socios_socio_id'))
        batch_op.drop_index('ix_credencial_socio_activa')
    op.drop_table('credenciales_socios')
