"""Фабрики мок-объектов, используемые в тестах."""
from unittest.mock import AsyncMock, MagicMock


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
