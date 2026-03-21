"""add notify_delta to chats

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column(
        'notify_delta',
        sa.Integer(),
        nullable=False,
        server_default='10',
    ))


def downgrade():
    op.drop_column('chats', 'notify_delta')
