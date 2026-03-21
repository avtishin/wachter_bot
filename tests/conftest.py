import os
import sys

# Должно быть ДО любых импортов из проекта — model.py создаёт engine при импорте
os.environ.setdefault("DATABASE_URL", "sqlite:///tests/test.db")
os.environ.setdefault("TELEGRAM_TOKEN", "0:test_token")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wachter"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from unittest.mock import AsyncMock, MagicMock
from helpers import make_message, make_update, make_kick_job  # noqa: F401 — реэкспорт для тестов


# --- DB fixtures ---

@pytest.fixture(scope="session", autouse=True)
def create_tables():
    from model import engine, Base
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    if os.path.exists("tests/test.db"):
        os.remove("tests/test.db")


@pytest.fixture(autouse=True)
def clean_db():
    yield
    from model import session_scope, Chat, User
    with session_scope() as sess:
        sess.query(User).delete()
        sess.query(Chat).delete()


# --- Bot / context fixtures ---

@pytest.fixture
def mock_bot():
    """Бот, где пользователь — обычный участник (не админ)."""
    bot = AsyncMock()
    member = MagicMock()
    member.status = "member"
    member.user = MagicMock()
    member.user.name = "Test User"
    member.user.mention_markdown = MagicMock(return_value="[Test User](tg://user?id=42)")
    bot.get_chat_member = AsyncMock(return_value=member)
    return bot


@pytest.fixture
def admin_bot():
    """Бот, где пользователь — администратор."""
    bot = AsyncMock()
    member = MagicMock()
    member.status = "administrator"
    member.user = MagicMock()
    member.user.name = "Admin User"
    member.user.mention_markdown = MagicMock(return_value="[Admin User](tg://user?id=100)")
    bot.get_chat_member = AsyncMock(return_value=member)
    return bot


@pytest.fixture
def mock_job_queue():
    jq = MagicMock()
    jq.get_jobs_by_name = MagicMock(return_value=[])
    return jq


@pytest.fixture
def mock_context(mock_bot, mock_job_queue):
    context = MagicMock()
    context.bot = mock_bot
    context.job_queue = mock_job_queue
    context.user_data = {}
    context.args = []
    return context


@pytest.fixture
def admin_context(admin_bot, mock_job_queue):
    context = MagicMock()
    context.bot = admin_bot
    context.job_queue = mock_job_queue
    context.user_data = {}
    context.args = []
    return context


# --- Update helpers ---

def make_message(chat_id=-100, user_id=42, text="test message"):
    message = MagicMock()
    message.chat_id = chat_id
    message.message_id = 999
    message.text = text
    message.caption = None
    message.reply_to_message = None
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.new_chat_members = []
    message.reply_text = AsyncMock()
    message.parse_entities = MagicMock(return_value={})
    message.text_markdown = text
    return message


def make_update(chat_id=-100, user_id=42, text="test message"):
    update = MagicMock()
    message = make_message(chat_id=chat_id, user_id=user_id, text=text)
    update.message = message
    update.effective_message = message
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def make_kick_job(chat_id=-100, user_id=42, message_id=888):
    job = MagicMock()
    job.data = {"chat_id": chat_id, "user_id": user_id, "message_id": message_id}
    return job
