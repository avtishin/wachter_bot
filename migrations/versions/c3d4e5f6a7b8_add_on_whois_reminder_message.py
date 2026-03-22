"""add on_whois_reminder_message

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column(
        'on_whois_reminder_message',
        sa.Text(),
        nullable=False,
        server_default=r'%USER\_MENTION%, напишите сообщение с тегом \#whois (минимум 20 символов), чтобы представиться.',
    ))


def downgrade():
    op.drop_column('chats', 'on_whois_reminder_message')
