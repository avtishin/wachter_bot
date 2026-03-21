"""add on_left_chat_member_message

Revision ID: a1b2c3d4e5f6
Revises: 0336b796d052
Create Date: 2026-03-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '0336b796d052'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column(
        'on_left_chat_member_message',
        sa.Text(),
        nullable=False,
        server_default=r'%USER\_MENTION% покинул чат',
    ))


def downgrade():
    op.drop_column('chats', 'on_left_chat_member_message')
