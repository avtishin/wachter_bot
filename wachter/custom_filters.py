from telegram.ext import filters


class FilterBotAdded(filters.BaseFilter):
    """Пропускает обновления о добавлении участников, когда добавляют НЕ бота."""
    def filter(self, message):
        return bool(message.new_chat_members and not message.new_chat_members[-1].is_bot)


filter_bot_added = FilterBotAdded()
