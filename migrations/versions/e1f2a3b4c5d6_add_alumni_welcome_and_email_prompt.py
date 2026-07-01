"""add on_alumni_welcome_message and on_email_prompt_message

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e1f2a3b4c5d6'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column(
        'on_alumni_welcome_message',
        sa.Text(),
        nullable=False,
        server_default='%NAME%, %CLASS% — добро пожаловать! 🎓',
    ))
    op.add_column('chats', sa.Column(
        'on_email_prompt_message',
        sa.Text(),
        nullable=False,
        server_default=(
            '📧 Введите ваш основной e-mail, указанный в профиле на my.nes.ru '
            '(раздел «Контактная информация»). Сверим его с директорией — если '
            'найдём, сразу вас узнаем. Не помните или не совпадёт — ничего '
            'страшного, продолжим вручную.'
        ),
    ))


def downgrade():
    op.drop_column('chats', 'on_email_prompt_message')
    op.drop_column('chats', 'on_alumni_welcome_message')
