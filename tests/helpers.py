"""Фабрики мок-объектов, используемые в тестах."""
from unittest.mock import AsyncMock, MagicMock


def telegram_markdown_ok(text):
    """Проверяет текст так же, как парсер legacy Markdown (parse_mode='Markdown')
    в Telegram: незакрытая сущность ``_ * ` `` или ```-блок, либо битая ссылка
    ``[text](url)`` дают ошибку «can't find end of the entity». Экранирование
    через ``\\`` делает символ литеральным. Возвращает True, если Telegram примет.

    Именно этот слой (валидность Markdown итогового сообщения), а не «есть ли
    подстрока X», ловит баги вроде незамещённого %USER_MENTION% с сырым `_`.
    """
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":            # экранированный литерал — пропускаем пару
            i += 2
            continue
        if text.startswith("```", i):   # pre-блок
            end = text.find("```", i + 3)
            if end == -1:
                return False
            i = end + 3
            continue
        if c in "_*`":           # сущность: нужен закрывающий такой же символ
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == c:
                    break
                j += 1
            if j >= n:
                return False     # незакрытая сущность — точная ошибка Telegram
            i = j + 1
            continue
        if c == "[":             # ссылка [text](url)
            close = text.find("]", i + 1)
            if close == -1 or close + 1 >= n or text[close + 1] != "(":
                return False
            paren = text.find(")", close + 2)
            if paren == -1:
                return False
            i = paren + 1
            continue
        i += 1
    return True


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
