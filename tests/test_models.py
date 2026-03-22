"""
Интеграционные тесты моделей и session_scope.
Используется SQLite in-memory (см. conftest.py).
"""
import pytest
from model import Chat, User, session_scope


class TestChatDefaults:
    def test_default_kick_timeout_is_zero(self):
        col = Chat.__table__.columns["kick_timeout"]
        assert col.default.arg == 0

    def test_default_filter_only_new_users_is_false(self):
        col = Chat.__table__.columns["filter_only_new_users"]
        assert col.default.arg is False

    def test_default_regex_filter_is_none(self):
        chat = Chat(id=-1)
        assert chat.regex_filter is None

    def test_default_welcome_message_has_timeout_placeholder(self):
        col = Chat.__table__.columns["on_new_chat_member_message"]
        assert "%TIMEOUT%" in col.default.arg

    def test_repr(self):
        assert "-100" in repr(Chat(id=-100))


class TestChatCRUD:
    def test_create_and_read(self):
        with session_scope() as sess:
            sess.add(Chat(id=-1001))

        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == -1001).first()
            assert chat is not None
            assert chat.kick_timeout == 0

    def test_update_via_merge(self):
        with session_scope() as sess:
            sess.add(Chat(id=-1002))

        with session_scope() as sess:
            sess.merge(Chat(id=-1002, kick_timeout=30, regex_filter=r"spam|buy"))

        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == -1002).first()
            assert chat.kick_timeout == 30
            assert chat.regex_filter == r"spam|buy"

    def test_update_filter_only_new_users(self):
        with session_scope() as sess:
            sess.add(Chat(id=-1003))

        with session_scope() as sess:
            sess.merge(Chat(id=-1003, filter_only_new_users=True))

        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == -1003).first()
            assert chat.filter_only_new_users is True

    def test_clear_regex_filter(self):
        with session_scope() as sess:
            sess.merge(Chat(id=-1004, regex_filter="spam"))

        with session_scope() as sess:
            sess.merge(Chat(id=-1004, regex_filter=None))

        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == -1004).first()
            assert chat.regex_filter is None


class TestUserCRUD:
    def test_create_and_read(self):
        with session_scope() as sess:
            sess.add(Chat(id=-2001))
            sess.add(User(chat_id=-2001, user_id=1, whois="Привет, я тест"))

        with session_scope() as sess:
            user = sess.query(User).filter(
                User.chat_id == -2001, User.user_id == 1
            ).first()
            assert user is not None
            assert user.whois == "Привет, я тест"

    def test_merge_updates_whois(self):
        with session_scope() as sess:
            sess.add(Chat(id=-2002))
            sess.merge(User(chat_id=-2002, user_id=2, whois="Первое представление"))

        with session_scope() as sess:
            sess.merge(User(chat_id=-2002, user_id=2, whois="Обновлённое представление"))

        with session_scope() as sess:
            user = sess.query(User).filter(
                User.chat_id == -2002, User.user_id == 2
            ).first()
            assert user.whois == "Обновлённое представление"

    def test_missing_user_returns_none(self):
        with session_scope() as sess:
            sess.add(Chat(id=-2003))

        with session_scope() as sess:
            user = sess.query(User).filter(
                User.chat_id == -2003, User.user_id == 999
            ).first()
            assert user is None

    def test_composite_primary_key(self):
        """Один пользователь может быть в двух чатах независимо."""
        with session_scope() as sess:
            sess.add(Chat(id=-2004))
            sess.add(Chat(id=-2005))
            sess.merge(User(chat_id=-2004, user_id=10, whois="Чат А"))
            sess.merge(User(chat_id=-2005, user_id=10, whois="Чат Б"))

        with session_scope() as sess:
            u1 = sess.query(User).filter(User.chat_id == -2004, User.user_id == 10).first()
            u2 = sess.query(User).filter(User.chat_id == -2005, User.user_id == 10).first()
            assert u1.whois == "Чат А"
            assert u2.whois == "Чат Б"


class TestSessionScope:
    def test_commits_on_success(self):
        with session_scope() as sess:
            sess.add(Chat(id=-3001))

        with session_scope() as sess:
            assert sess.query(Chat).filter(Chat.id == -3001).first() is not None

    def test_rolls_back_on_exception(self):
        with pytest.raises(ValueError):
            with session_scope() as sess:
                sess.add(Chat(id=-3002))
                raise ValueError("ошибка в транзакции")

        with session_scope() as sess:
            assert sess.query(Chat).filter(Chat.id == -3002).first() is None

    def test_multiple_operations_in_one_scope(self):
        with session_scope() as sess:
            sess.add(Chat(id=-3003))
            sess.merge(User(chat_id=-3003, user_id=1, whois="test"))
            sess.merge(User(chat_id=-3003, user_id=2, whois="test2"))

        with session_scope() as sess:
            count = sess.query(User).filter(User.chat_id == -3003).count()
            assert count == 2
