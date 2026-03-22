"""add min_whois_length, ban_duration, on_filtered_message

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column('min_whois_length', sa.Integer(), nullable=False, server_default='20'))
    op.add_column('chats', sa.Column('ban_duration', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('chats', sa.Column(
        'on_filtered_message',
        sa.Text(),
        nullable=False,
        server_default=r'%USER\_MENTION%, вы были забанены т.к ваше сообщение содержит репост или слово из спам листа',
    ))


def downgrade():
    op.drop_column('chats', 'min_whois_length')
    op.drop_column('chats', 'ban_duration')
    op.drop_column('chats', 'on_filtered_message')
