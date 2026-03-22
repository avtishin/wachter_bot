from telegram.ext import filters


class FilterBotAdded(filters.BaseFilter):
    """Пропускает обновления о добавлении участников, когда среди добавленных есть хотя бы один НЕ-бот."""
    def filter(self, message):
        return bool(message.new_chat_members and any(not m.is_bot for m in message.new_chat_members))


filter_bot_added = FilterBotAdded()
