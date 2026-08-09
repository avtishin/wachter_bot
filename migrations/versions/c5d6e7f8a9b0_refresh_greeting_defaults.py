"""refresh greeting/reminder texts to the new %NAME%/%CLASS% + anketa wording

Updates existing chat rows that still hold the OLD default text to the NEW
default, so deployed chats pick up the reworded greetings without losing any
admin customisation (only exact-old-default rows are touched).

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c5d6e7f8a9b0'
down_revision = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


# (column, old default, new default) — обновляем только строки со старым дефолтом
_UPDATES = [
    ("on_introduce_message",
     '🤍 Добро пожаловать в Мишпуху 2.0!',
     '%USER\\_MENTION%, %CLASS% — добро пожаловать! 🎓\n%NAME%'),
    ("on_known_new_chat_member_message",
     'Добро пожаловать. Снова',
     '%NAME%, %CLASS% — с возвращением! 🎓'),
    ("on_whois_reminder_message",
     r'%USER\_MENTION%, напишите сообщение с тегом \#whois (минимум %MIN\_LENGTH% символов), чтобы представиться.',
     r'%USER\_MENTION%, вы ещё не закончили знакомство — заполните короткую анкету кнопками выше.'),
    ("notify_message",
     r'%USER\_MENTION%, пожалуйста, представьтесь и поздоровайтесь с сообществом.',
     r'%USER\_MENTION%, вы ещё не закончили знакомство. Пожалуйста, продолжите и заполните анкету — иначе через %MINUTES% мин. я удалю вас из чата.'),
]


def upgrade():
    conn = op.get_bind()
    for col, old, new in _UPDATES:
        conn.execute(
            sa.text(f"UPDATE chats SET {col} = :new WHERE {col} = :old"),
            {"new": new, "old": old})


def downgrade():
    # forward-only data fix — не откатываем тексты
    pass
