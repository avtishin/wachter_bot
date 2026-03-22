"""
Юнит-тесты вспомогательных функций actions.py.
Внешние зависимости (bot API, БД) заменяются моками.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# authorize_user
# ---------------------------------------------------------------------------

class TestAuthorizeUser:
    async def test_creator_authorized(self, mock_bot):
        from actions import authorize_user
        mock_bot.get_chat_member.return_value.status = "creator"
        assert await authorize_user(mock_bot, -100, 42)

    async def test_administrator_authorized(self, mock_bot):
        from actions import authorize_user
        mock_bot.get_chat_member.return_value.status = "administrator"
        assert await authorize_user(mock_bot, -100, 42)

    async def test_member_not_authorized(self, mock_bot):
        from actions import authorize_user
        mock_bot.get_chat_member.return_value.status = "member"
        assert not await authorize_user(mock_bot, -100, 42)

    async def test_kicked_not_authorized(self, mock_bot):
        from actions import authorize_user
        mock_bot.get_chat_member.return_value.status = "kicked"
        assert not await authorize_user(mock_bot, -100, 42)

    async def test_api_exception_returns_false(self, mock_bot):
        from actions import authorize_user
        mock_bot.get_chat_member.side_effect = Exception("Telegram API error")
        assert not await authorize_user(mock_bot, -100, 42)


# ---------------------------------------------------------------------------
# mention_markdown
# ---------------------------------------------------------------------------

class TestMentionMarkdown:
    async def test_replaces_placeholder(self, mock_bot):
        from actions import mention_markdown
        mock_bot.get_chat_member.return_value.user.name = "Alice"
        mock_bot.get_chat_member.return_value.user.mention_markdown.return_value = "[Alice](tg://user?id=1)"
        result = await mention_markdown(mock_bot, -100, 1, "Привет, %USER\\_MENTION%!")
        assert "[Alice](tg://user?id=1)" in result
        assert "%USER\\_MENTION%" not in result

    async def test_deleted_user_empty_mention(self, mock_bot):
        from actions import mention_markdown
        mock_bot.get_chat_member.return_value.user.name = None
        result = await mention_markdown(mock_bot, -100, 1, "Привет, %USER\\_MENTION%!")
        assert "%USER\\_MENTION%" not in result
        assert result == "Привет, !"

    async def test_api_exception_empty_mention(self, mock_bot):
        from actions import mention_markdown
        mock_bot.get_chat_member.side_effect = Exception("user left")
        result = await mention_markdown(mock_bot, -100, 1, "%USER\\_MENTION% вышел")
        assert result == " вышел"


# ---------------------------------------------------------------------------
# cancel_kick_jobs
# ---------------------------------------------------------------------------

class TestCancelKickJobs:
    async def test_cancels_kick_job(self, mock_bot, mock_job_queue):
        from actions import cancel_kick_jobs
        job = MagicMock()
        job.data = {"chat_id": -100, "user_id": 42, "message_id": 888}
        mock_job_queue.get_jobs_by_name.side_effect = (
            lambda name: [job] if "kick" in name else []
        )
        result = await cancel_kick_jobs(mock_bot, mock_job_queue, -100, 42)
        assert result is True
        job.schedule_removal.assert_called_once()
        mock_bot.delete_message.assert_called_once_with(-100, 888)

    async def test_cancels_notify_job(self, mock_bot, mock_job_queue):
        from actions import cancel_kick_jobs
        job = MagicMock()
        job.data = {"chat_id": -100, "user_id": 42}  # без message_id
        mock_job_queue.get_jobs_by_name.side_effect = (
            lambda name: [job] if "notify" in name else []
        )
        result = await cancel_kick_jobs(mock_bot, mock_job_queue, -100, 42)
        assert result is True
        mock_bot.delete_message.assert_not_called()  # нет message_id — удалять нечего

    async def test_returns_false_when_no_jobs(self, mock_bot, mock_job_queue):
        from actions import cancel_kick_jobs
        mock_job_queue.get_jobs_by_name.return_value = []
        assert not await cancel_kick_jobs(mock_bot, mock_job_queue, -100, 42)

    async def test_delete_exception_does_not_raise(self, mock_bot, mock_job_queue):
        from actions import cancel_kick_jobs
        job = MagicMock()
        job.data = {"chat_id": -100, "user_id": 42, "message_id": 888}
        mock_job_queue.get_jobs_by_name.side_effect = (
            lambda name: [job] if "kick" in name else []
        )
        mock_bot.delete_message.side_effect = Exception("already deleted")
        # Не должно бросить исключение
        result = await cancel_kick_jobs(mock_bot, mock_job_queue, -100, 42)
        assert result is True

    async def test_only_cancels_matching_user(self, mock_bot, mock_job_queue):
        """get_jobs_by_name использует имя — чужие джобы не затрагиваются."""
        from actions import cancel_kick_jobs
        mock_job_queue.get_jobs_by_name.return_value = []
        result = await cancel_kick_jobs(mock_bot, mock_job_queue, -100, 999)
        assert result is False
        mock_job_queue.get_jobs_by_name.assert_any_call("kick_-100_999")
        mock_job_queue.get_jobs_by_name.assert_any_call("notify_-100_999")


# ---------------------------------------------------------------------------
# filter_message
# ---------------------------------------------------------------------------

class TestFilterMessage:
    def _mock_chat(self, regex):
        chat = MagicMock()
        chat.regex_filter = regex
        return chat

    def test_empty_text_returns_false(self):
        from actions import filter_message
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = self._mock_chat("spam")
            assert not filter_message(-100, None)
            assert not filter_message(-100, "")

    def test_no_regex_filter_returns_false(self):
        from actions import filter_message
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = self._mock_chat(None)
            assert not filter_message(-100, "купи крипту срочно")

    def test_matching_text_returns_truthy(self):
        from actions import filter_message
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = self._mock_chat(r"крипт|invest|заработ")
            assert filter_message(-100, "купи крипту срочно")

    def test_non_matching_text_returns_false(self):
        from actions import filter_message
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = self._mock_chat(r"крипт|invest")
            assert not filter_message(-100, "привет всем, я новый участник")

    def test_none_chat_returns_false(self):
        from actions import filter_message
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
            assert not filter_message(-100, "любое сообщение")

    def test_invalid_regex_returns_false(self):
        """Невалидная regex не должна бросать исключение."""
        from actions import filter_message
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = self._mock_chat(r"(unclosed")
            assert not filter_message(-100, "любое сообщение")


# ---------------------------------------------------------------------------
# is_new_user
# ---------------------------------------------------------------------------

class TestIsNewUser:
    def test_user_not_in_db_is_new(self):
        from actions import is_new_user
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
            assert is_new_user(-100, 42)

    def test_user_in_db_is_not_new(self):
        from actions import is_new_user
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = MagicMock()
            assert not is_new_user(-100, 42)


# ---------------------------------------------------------------------------
# is_chat_filters_new_users
# ---------------------------------------------------------------------------

class TestIsChatFiltersNewUsers:
    def test_returns_true_when_enabled(self):
        from actions import is_chat_filters_new_users
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.scalar.return_value = True
            assert is_chat_filters_new_users(-100)

    def test_returns_false_when_disabled(self):
        from actions import is_chat_filters_new_users
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.scalar.return_value = False
            assert not is_chat_filters_new_users(-100)

    def test_returns_false_when_chat_missing(self):
        from actions import is_chat_filters_new_users
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.scalar.return_value = None
            assert not is_chat_filters_new_users(-100)


# ---------------------------------------------------------------------------
# custom_filters
# ---------------------------------------------------------------------------

class TestFilterBotAdded:
    def _make_message(self, members):
        msg = MagicMock()
        msg.new_chat_members = members
        return msg

    def _member(self, is_bot=False):
        m = MagicMock()
        m.is_bot = is_bot
        return m

    def test_single_real_user_passes(self):
        from custom_filters import filter_bot_added
        msg = self._make_message([self._member(is_bot=False)])
        assert filter_bot_added.filter(msg)

    def test_single_bot_blocked(self):
        from custom_filters import filter_bot_added
        msg = self._make_message([self._member(is_bot=True)])
        assert not filter_bot_added.filter(msg)

    def test_user_and_bot_together_passes(self):
        """Если одновременно добавлены пользователь и бот — пропускаем (есть живой участник)."""
        from custom_filters import filter_bot_added
        msg = self._make_message([self._member(False), self._member(True)])
        assert filter_bot_added.filter(msg)

    def test_only_bots_blocked(self):
        from custom_filters import filter_bot_added
        msg = self._make_message([self._member(True), self._member(True)])
        assert not filter_bot_added.filter(msg)

    def test_empty_members_blocked(self):
        from custom_filters import filter_bot_added
        msg = self._make_message([])
        assert not filter_bot_added.filter(msg)


# ---------------------------------------------------------------------------
# on_skip — anonymous from_user
# ---------------------------------------------------------------------------

class TestSkipAnonymous:
    async def test_anonymous_reply_shows_error(self, mock_context):
        """Ответ на сообщение анонимного администратора не вызывает crash."""
        from actions import on_skip_command
        from helpers import make_update
        update = make_update(chat_id=-100, user_id=42)
        update.effective_message.reply_to_message = MagicMock()
        update.effective_message.reply_to_message.from_user = None
        await on_skip_command(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(
            "Невозможно определить пользователя (анонимное сообщение)."
        )

    async def test_anonymous_issuer_shows_error(self, mock_context):
        """Анонимный администратор, отправляющий /skip, не вызывает crash."""
        from actions import on_skip_command
        from helpers import make_update
        update = make_update(chat_id=-100, user_id=42)
        update.effective_message.from_user = None
        target = MagicMock()
        target.from_user = MagicMock()
        target.from_user.id = 99
        update.effective_message.reply_to_message = target
        await on_skip_command(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(
            "Невозможно определить администратора (анонимное сообщение)."
        )


# ---------------------------------------------------------------------------
# on_approve — anonymous from_user
# ---------------------------------------------------------------------------

class TestApproveAnonymous:
    async def test_anonymous_issuer_shows_error(self, mock_context):
        """Анонимный администратор, отправляющий /approve, не вызывает crash."""
        from actions import on_approve_command
        from helpers import make_update
        update = make_update(chat_id=-100)
        update.effective_message.from_user = None
        await on_approve_command(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(
            "Невозможно определить администратора (анонимное сообщение)."
        )
