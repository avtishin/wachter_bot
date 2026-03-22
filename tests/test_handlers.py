"""
Тесты хендлеров: каждый хендлер проверяется с мок-объектами Update и Context.
БД замокана через patch('actions.session_scope').
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from helpers import make_update, make_message, make_kick_job
import constants


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

class TestHelpCommand:
    async def test_sends_help_message(self, mock_context):
        from actions import on_help_command
        update = make_update()
        await on_help_command(update, mock_context)
        update.message.reply_text.assert_called_once_with(constants.help_message)


# ---------------------------------------------------------------------------
# /skip
# ---------------------------------------------------------------------------

class TestSkipCommand:
    async def test_ignores_private_chat(self, mock_context):
        from actions import on_skip_command
        update = make_update(chat_id=42)  # DM: chat_id > 0
        await on_skip_command(update, mock_context)
        update.effective_message.reply_text.assert_not_called()

    async def test_no_reply_shows_hint(self, mock_context):
        from actions import on_skip_command
        update = make_update(chat_id=-100)
        update.effective_message.reply_to_message = None
        await on_skip_command(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(constants.on_failed_skip)

    async def test_non_admin_cannot_skip(self, mock_context):
        from actions import on_skip_command
        update = make_update(chat_id=-100, user_id=42)
        update.effective_message.reply_to_message = MagicMock()
        update.effective_message.reply_to_message.from_user.id = 99
        # mock_bot.status = "member" по умолчанию
        await on_skip_command(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(
            "Эта команда доступна только администраторам."
        )

    async def test_admin_skip_cancels_job_and_replies(self, admin_context):
        from actions import on_skip_command
        update = make_update(chat_id=-100, user_id=100)
        update.effective_message.reply_to_message = MagicMock()
        update.effective_message.reply_to_message.from_user.id = 99

        job = make_kick_job(chat_id=-100, user_id=99)
        admin_context.job_queue.get_jobs_by_name.side_effect = (
            lambda name: [job] if "kick" in name else []
        )
        await on_skip_command(update, admin_context)
        update.effective_message.reply_text.assert_called_once_with(constants.on_success_skip)

    async def test_admin_skip_no_pending_job_no_reply(self, admin_context):
        from actions import on_skip_command
        update = make_update(chat_id=-100, user_id=100)
        update.effective_message.reply_to_message = MagicMock()
        update.effective_message.reply_to_message.from_user.id = 99
        admin_context.job_queue.get_jobs_by_name.return_value = []
        await on_skip_command(update, admin_context)
        update.effective_message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# /approve
# ---------------------------------------------------------------------------

class TestApproveCommand:
    async def test_ignores_private_chat(self, admin_context):
        from actions import on_approve_command
        update = make_update(chat_id=100)  # DM
        await on_approve_command(update, admin_context)
        update.effective_message.reply_text.assert_not_called()

    async def test_non_admin_ignored(self, mock_context):
        from actions import on_approve_command
        update = make_update(chat_id=-100)
        await on_approve_command(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(
            "Эта команда доступна только администраторам."
        )

    async def test_no_reply_shows_hint(self, admin_context):
        from actions import on_approve_command
        update = make_update(chat_id=-100)
        update.effective_message.reply_to_message = None
        await on_approve_command(update, admin_context)
        update.effective_message.reply_text.assert_called_once_with(
            "Ответьте на сообщение пользователя, которого нужно одобрить."
        )

    async def test_approve_saves_user_and_replies(self, admin_context):
        from actions import on_approve_command
        update = make_update(chat_id=-100, user_id=100)
        update.effective_message.reply_to_message = MagicMock()
        update.effective_message.reply_to_message.from_user.id = 99
        update.effective_message.reply_to_message.from_user.is_bot = False

        with patch("actions.session_scope") as mock_scope:
            mock_sess = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_approve_command(update, admin_context)

        mock_sess.merge.assert_called_once()
        merged = mock_sess.merge.call_args[0][0]
        assert merged.user_id == 99
        assert merged.chat_id == -100
        update.effective_message.reply_text.assert_called_once_with("Пользователь одобрен.")

    async def test_approve_cancels_kick_job(self, admin_context):
        from actions import on_approve_command
        update = make_update(chat_id=-100, user_id=100)
        update.effective_message.reply_to_message = MagicMock()
        update.effective_message.reply_to_message.from_user.id = 99
        update.effective_message.reply_to_message.from_user.is_bot = False

        job = make_kick_job(chat_id=-100, user_id=99)
        admin_context.job_queue.get_jobs_by_name.side_effect = (
            lambda name: [job] if "kick" in name else []
        )
        with patch("actions.session_scope"):
            await on_approve_command(update, admin_context)

        job.schedule_removal.assert_called_once()


# ---------------------------------------------------------------------------
# #whois
# ---------------------------------------------------------------------------

class TestHashtagMessage:
    def _make_whois_update(self, text, chat_id=-100, user_id=42):
        update = make_update(chat_id=chat_id, user_id=user_id, text=text)
        update.effective_message.parse_entities = MagicMock(
            return_value={"0:6": "#whois"}
        )
        update.effective_message.text = text
        return update

    async def test_too_short_falls_through_to_on_message(self, mock_context):
        from actions import on_hashtag_message
        update = self._make_whois_update("#whois hi")  # < 20 символов
        with patch("actions.on_message", new_callable=AsyncMock) as mock_on_msg:
            await on_hashtag_message(update, mock_context)
            mock_on_msg.assert_called_once()

    async def test_valid_whois_saves_user(self, mock_context):
        from actions import on_hashtag_message
        long_text = "#whois " + "Привет, меня зовут Александр, я разработчик"
        update = self._make_whois_update(long_text)

        mock_chat = MagicMock()
        mock_chat.on_introduce_message = "Добро пожаловать."
        mock_chat.min_whois_length = 20
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.return_value = mock_chat

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_hashtag_message(update, mock_context)

        mock_sess.merge.assert_called_once()

    async def test_valid_whois_with_pending_job_replies(self, mock_context):
        from actions import on_hashtag_message
        long_text = "#whois " + "Привет, меня зовут Александр, я разработчик"
        update = self._make_whois_update(long_text)

        job = make_kick_job(chat_id=-100, user_id=42)
        mock_context.job_queue.get_jobs_by_name.side_effect = (
            lambda name: [job] if "kick" in name else []
        )
        mock_chat = MagicMock()
        mock_chat.on_introduce_message = "Добро пожаловать."
        mock_chat.min_whois_length = 20
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.return_value = mock_chat

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_hashtag_message(update, mock_context)

        update.effective_message.reply_text.assert_called_once()

    async def test_valid_whois_without_pending_job_no_reply(self, mock_context):
        """Пользователь написал #whois, но таймер уже не висит — молча записываем."""
        from actions import on_hashtag_message
        long_text = "#whois " + "Привет, меня зовут Александр, я разработчик"
        update = self._make_whois_update(long_text)
        mock_context.job_queue.get_jobs_by_name.return_value = []

        mock_chat = MagicMock()
        mock_chat.on_introduce_message = "Добро пожаловать."
        mock_chat.min_whois_length = 20
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.return_value = mock_chat

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_hashtag_message(update, mock_context)

        update.effective_message.reply_text.assert_not_called()

    async def test_dm_hashtag_falls_through(self, mock_context):
        """В личке #whois не обрабатывается."""
        from actions import on_hashtag_message
        long_text = "#whois " + "Привет, меня зовут Александр, я разработчик"
        update = self._make_whois_update(long_text, chat_id=42)  # DM

        with patch("actions.on_message", new_callable=AsyncMock) as mock_on_msg:
            await on_hashtag_message(update, mock_context)
            mock_on_msg.assert_called_once()


# ---------------------------------------------------------------------------
# /whois
# ---------------------------------------------------------------------------

class TestWhoisCommand:
    async def test_no_args_shows_usage(self, mock_context):
        from actions import on_whois_command
        update = make_update(chat_id=-100)
        mock_context.args = []
        update.message.reply_to_message = None
        await on_whois_command(update, mock_context)
        update.message.reply_text.assert_called_once_with(
            "Usage: /whois @username | /whois <user_id> | ответ на сообщение"
        )

    async def test_extra_args_uses_first_arg(self, mock_context):
        from actions import on_whois_command
        update = make_update(chat_id=-100)
        mock_context.args = ["42", "extra"]

        with patch("actions.session_scope") as mock_scope:
            mock_sess = MagicMock()
            mock_sess.query.return_value.filter.return_value.first.return_value = None
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_whois_command(update, mock_context)

        update.message.reply_text.assert_called_once_with("Пользователь не найден в базе.")

    async def test_user_found(self, mock_context):
        from actions import on_whois_command
        update = make_update(chat_id=-100)
        mock_context.args = ["42"]
        mock_user = MagicMock()
        mock_user.whois = "Привет, я Алиса, фронтенд-разработчик"

        with patch("actions.session_scope") as mock_scope:
            mock_sess = MagicMock()
            mock_sess.query.return_value.filter.return_value.first.return_value = mock_user
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_whois_command(update, mock_context)

        update.message.reply_text.assert_called_once_with(
            "whois: Привет, я Алиса, фронтенд-разработчик"
        )

    async def test_user_not_found(self, mock_context):
        from actions import on_whois_command
        update = make_update(chat_id=-100)
        mock_context.args = ["999"]

        with patch("actions.session_scope") as mock_scope:
            mock_sess = MagicMock()
            mock_sess.query.return_value.filter.return_value.first.return_value = None
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_whois_command(update, mock_context)

        update.message.reply_text.assert_called_once_with("Пользователь не найден в базе.")


# ---------------------------------------------------------------------------
# on_new_chat_member
# ---------------------------------------------------------------------------

class TestNewChatMember:
    def _make_new_member_update(self, chat_id=-100, user_ids=(42,)):
        update = make_update(chat_id=chat_id)
        members = []
        for uid in user_ids:
            m = MagicMock()
            m.id = uid
            m.is_bot = False
            members.append(m)
        update.message.new_chat_members = members
        update.effective_chat.id = chat_id
        return update

    async def test_unknown_user_gets_welcome(self, mock_context):
        from actions import on_new_chat_member
        update = self._make_new_member_update()

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = "Привет %USER\\_MENTION%! Есть %TIMEOUT%."
        mock_chat.on_known_new_chat_member_message = "Снова привет."
        mock_chat.kick_timeout = 0
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.side_effect = [
            mock_chat,  # Chat query
            None,       # User query — нового пользователя нет в БД
        ]

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        update.message.reply_text.assert_called_once()

    async def test_known_user_gets_known_message(self, mock_context):
        from actions import on_new_chat_member
        update = self._make_new_member_update()

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = "Привет %USER\\_MENTION%!"
        mock_chat.on_known_new_chat_member_message = "Снова привет."
        mock_chat.kick_timeout = 0
        mock_user = MagicMock()
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.side_effect = [
            mock_chat,
            mock_user,  # пользователь уже есть в БД
        ]

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        update.message.reply_text.assert_called_once_with("Снова привет.")

    async def test_multiple_members_all_processed(self, mock_context):
        from actions import on_new_chat_member
        update = self._make_new_member_update(user_ids=(1, 2, 3))

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = "Привет %USER\\_MENTION%!"
        mock_chat.on_known_new_chat_member_message = "Снова."
        mock_chat.kick_timeout = 0
        mock_sess = MagicMock()
        # Chat один раз, затем 3 раза User (для каждого участника)
        mock_sess.query.return_value.filter.return_value.first.side_effect = [
            mock_chat, None, None, None
        ]

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        assert update.message.reply_text.call_count == 3

    async def test_bot_member_skipped(self, mock_context):
        """Бот в списке new_chat_members не должен получать приветствие."""
        from actions import on_new_chat_member
        update = make_update(chat_id=-100)
        bot_member = MagicMock()
        bot_member.id = 555
        bot_member.is_bot = True
        update.message.new_chat_members = [bot_member]
        update.effective_chat.id = -100

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = "Привет!"
        mock_chat.on_known_new_chat_member_message = "Снова."
        mock_chat.kick_timeout = 0
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.return_value = mock_chat

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        update.message.reply_text.assert_not_called()

    async def test_skip_message_sends_nothing(self, mock_context):
        from actions import on_new_chat_member
        import constants as c
        update = self._make_new_member_update()

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = c.skip_on_new_chat_member_message
        mock_chat.on_known_new_chat_member_message = "Снова."
        mock_chat.kick_timeout = 0
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.side_effect = [
            mock_chat, None
        ]

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        update.message.reply_text.assert_not_called()

    async def test_kick_timeout_schedules_jobs(self, mock_context):
        from actions import on_new_chat_member
        update = self._make_new_member_update()
        update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=777))

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = "Привет %USER\\_MENTION%!"
        mock_chat.on_known_new_chat_member_message = "Снова."
        mock_chat.kick_timeout = 30
        mock_chat.notify_delta = 10
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.side_effect = [
            mock_chat, None
        ]

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        # Должны быть запланированы notify и kick
        assert mock_context.job_queue.run_once.call_count == 2
        names = [
            call.kwargs.get("name", "")
            for call in mock_context.job_queue.run_once.call_args_list
        ]
        assert any("notify" in n for n in names)
        assert any("kick" in n for n in names)

    async def test_timeout_placeholder_replaced(self, mock_context):
        from actions import on_new_chat_member
        update = self._make_new_member_update()
        sent_texts = []

        async def capture_reply(text, **kwargs):
            sent_texts.append(text)
            return MagicMock(message_id=777)

        update.message.reply_text = capture_reply

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = "Есть %TIMEOUT% на представление."
        mock_chat.on_known_new_chat_member_message = "Снова."
        mock_chat.kick_timeout = 15
        mock_chat.notify_delta = 10
        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.side_effect = [
            mock_chat, None
        ]

        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        assert len(sent_texts) == 1
        assert "15 мин." in sent_texts[0]
        assert "%TIMEOUT%" not in sent_texts[0]


# ---------------------------------------------------------------------------
# on_forward
# ---------------------------------------------------------------------------

class TestOnForward:
    def _make_forward_update(self, chat_id=-100, user_id=42):
        update = make_update(chat_id=chat_id, user_id=user_id)
        update.effective_message.chat_id = chat_id
        update.effective_message.from_user.id = user_id
        update.effective_message.message_id = 555
        return update

    def _mock_chat_with_regex(self, regex="spam", filter_only_new=False):
        chat = MagicMock()
        chat.regex_filter = regex
        chat.filter_only_new_users = filter_only_new
        chat.on_filtered_message = r"%USER\_MENTION% забанен"
        chat.ban_duration = 1
        return chat

    async def test_admin_is_not_filtered(self, admin_context):
        from actions import on_forward
        update = self._make_forward_update()
        # admin_context.bot возвращает creator — не должен быть забанен
        await on_forward(update, admin_context)
        admin_context.bot.ban_chat_member.assert_not_called()

    async def test_dm_is_ignored(self, mock_context):
        from actions import on_forward
        update = self._make_forward_update(chat_id=42)  # DM
        await on_forward(update, mock_context)
        mock_context.bot.ban_chat_member.assert_not_called()

    async def test_no_regex_filter_ignores(self, mock_context):
        from actions import on_forward
        update = self._make_forward_update()
        mock_chat = self._mock_chat_with_regex(regex=None)
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = mock_chat
            await on_forward(update, mock_context)
        mock_context.bot.ban_chat_member.assert_not_called()

    async def test_new_user_with_regex_is_banned(self, mock_context):
        from actions import on_forward
        update = self._make_forward_update()
        mock_chat = self._mock_chat_with_regex(filter_only_new=False)
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = mock_chat
            with patch("actions.is_new_user", return_value=True):
                await on_forward(update, mock_context)
        mock_context.bot.ban_chat_member.assert_called_once()

    async def test_known_user_skipped_when_filter_only_new(self, mock_context):
        from actions import on_forward
        update = self._make_forward_update()
        mock_chat = self._mock_chat_with_regex(filter_only_new=True)
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = mock_chat
            with patch("actions.is_new_user", return_value=False):
                await on_forward(update, mock_context)
        mock_context.bot.ban_chat_member.assert_not_called()

    async def test_known_user_banned_when_filter_all(self, mock_context):
        from actions import on_forward
        update = self._make_forward_update()
        mock_chat = self._mock_chat_with_regex(filter_only_new=False)
        with patch("actions.session_scope") as m:
            m.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = mock_chat
            with patch("actions.is_new_user", return_value=False):
                await on_forward(update, mock_context)
        mock_context.bot.ban_chat_member.assert_called_once()
