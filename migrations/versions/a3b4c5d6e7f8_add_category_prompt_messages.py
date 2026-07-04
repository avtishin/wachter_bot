"""add per-category whois prompts (student / friend / employee)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a3b4c5d6e7f8'
down_revision = 'f2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column(
        'on_student_prompt_message', sa.Text(), nullable=False,
        server_default='Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: '
                       'что окончили или чем занимались до РЭШ, где сейчас живёте и '
                       'чем увлекаетесь во внеучебное время.'))
    op.add_column('chats', sa.Column(
        'on_friend_prompt_message', sa.Text(), nullable=False,
        server_default='Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: '
                       'что связывает вас с РЭШ и чем вы занимаетесь.'))
    op.add_column('chats', sa.Column(
        'on_employee_prompt_message', sa.Text(), nullable=False,
        server_default='Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: '
                       'чем вы занимаетесь в РЭШ.'))


def downgrade():
    op.drop_column('chats', 'on_employee_prompt_message')
    op.drop_column('chats', 'on_friend_prompt_message')
    op.drop_column('chats', 'on_student_prompt_message')
