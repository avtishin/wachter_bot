"""add on_whois_welcome_message and on_whois_name_message

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f2a3b4c5d6e7'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column(
        'on_whois_welcome_message', sa.Text(), nullable=False,
        server_default='Привет, %USER_MENTION%.\nРады видеть тебя в Мишпухе 2.0 🤍\n\n'
                       'Это чат студентов, выпускников, сотрудников и друзей РЭШ.\n'
                       'Давайте познакомимся — выберите, кто вы (у вас есть %TIMEOUT%):'))
    op.add_column('chats', sa.Column(
        'on_whois_name_message', sa.Text(), nullable=False,
        server_default='Напишите, пожалуйста, ваши Фамилию и Имя, а также пару слов о себе: '
                       'где вы сейчас живёте и работаете, чем занимаетесь и в чём ваша экспертиза.'))


def downgrade():
    op.drop_column('chats', 'on_whois_name_message')
    op.drop_column('chats', 'on_whois_welcome_message')
