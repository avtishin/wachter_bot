"""add chats.title (chat name for the dashboard participants selector)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-07-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b4c5d6e7f8a9'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column('title', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('chats', 'title')
