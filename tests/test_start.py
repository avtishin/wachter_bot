"""/start меню чатов + bootstrap строки chats — система прав/управления.

Ключевое поведение: настраивать бота может любой Telegram-админ чата, где бот
присутствует (строка в `chats`), даже если сам админ ни разу не писал #whois
(в таблице `users` его нет)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from model import session_scope, Chat
import actions
from helpers import make_update


def _bot_admin_of(admin_chat_ids):
    """Бот-мок: user — админ в перечисленных чатах, участник в остальных."""
    bot = AsyncMock()

    async def get_chat_member(chat_id, user_id):
        status = "administrator" if chat_id in admin_chat_ids else "member"
        return SimpleNamespace(status=status)

    async def get_chat(chat_id):
        return SimpleNamespace(title=f"Chat {chat_id}")

    bot.get_chat_member = AsyncMock(side_effect=get_chat_member)
    bot.get_chat = AsyncMock(side_effect=get_chat)
    return bot


def _private_update(user_id=42):
    update = make_update(chat_id=1, user_id=user_id)   # приватный чат (id > 0)
    update.effective_chat.id = 1
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


async def test_start_lists_admin_chats_without_users_row():
    # в users пусто — раньше это давало «нет доступных чатов»
    with session_scope() as s:
        s.add(Chat(id=-100))   # тут админ
        s.add(Chat(id=-200))   # тут просто участник
    ctx = MagicMock()
    ctx.bot = _bot_admin_of({-100})
    update = _private_update()

    await actions.on_start_command(update, ctx)

    kwargs = update.message.reply_text.call_args[1]
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Chat -100"]   # только чат, где user — админ


async def test_start_no_admin_chats_says_none():
    with session_scope() as s:
        s.add(Chat(id=-100))
    ctx = MagicMock()
    ctx.bot = _bot_admin_of(set())   # нигде не админ
    update = _private_update()

    await actions.on_start_command(update, ctx)

    update.message.reply_text.assert_called_once_with("У вас нет доступных чатов.")


def _my_chat_member_update(chat_id, status):
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=chat_id)
    update.my_chat_member = SimpleNamespace(
        new_chat_member=SimpleNamespace(status=status))
    return update


async def test_bot_added_creates_chat_row(mock_context):
    await actions.on_my_chat_member(_my_chat_member_update(-555, "administrator"), mock_context)
    with session_scope() as s:
        assert s.get(Chat, -555) is not None   # готово к настройке через /start


async def test_bot_removed_does_not_create_chat_row(mock_context):
    await actions.on_my_chat_member(_my_chat_member_update(-556, "left"), mock_context)
    with session_scope() as s:
        assert s.get(Chat, -556) is None


async def test_bot_added_stores_chat_title(mock_context):
    upd = MagicMock()
    upd.effective_chat = SimpleNamespace(id=-600, title="Мишпуха 2.0")
    upd.my_chat_member = SimpleNamespace(
        new_chat_member=SimpleNamespace(status="administrator"))
    await actions.on_my_chat_member(upd, mock_context)
    with session_scope() as s:
        assert s.get(Chat, -600).title == "Мишпуха 2.0"


# --- редактирование текстов: покрытие меню и «текущих настроек» ---

def test_all_editable_columns_covered_by_menu_and_settings():
    import constants
    from model import Chat
    # намеренно не редактируется: PK, название (бот пишет сам), мёртвая (%SKIP%)
    skip = {"id", "title", "on_new_chat_member_message"}
    field_names = set(actions.ACTION_FIELD.values())
    for c in Chat.__table__.columns:
        if c.name in skip:
            continue
        assert c.name in field_names, f"{c.name} нельзя отредактировать через меню"
        assert "{" + c.name + "}" in constants.get_settings_message, \
            f"{c.name} не показан в «текущих настройках»"


# --- выход участника через chat_member-апдейт ---

def _left_update(chat_id, user_id, old="member", new="left", from_id=42):
    user = SimpleNamespace(id=user_id, is_bot=False, name="Alex", full_name="Alex")
    update = MagicMock()
    update.chat_member = SimpleNamespace(
        old_chat_member=SimpleNamespace(status=old),
        new_chat_member=SimpleNamespace(status=new, user=user),
        from_user=SimpleNamespace(id=from_id))
    update.effective_chat = SimpleNamespace(id=chat_id)
    return update


async def test_member_left_posts_message(mock_context):
    mock_context.bot.id = 999
    with session_scope() as s:
        s.add(Chat(id=-100, on_left_chat_member_message=r"%USER\_MENTION% покинул чат"))
    await actions.on_chat_member_update(_left_update(-100, 42, from_id=42), mock_context)
    mock_context.bot.send_message.assert_called_once()
    assert "покинул чат" in mock_context.bot.send_message.call_args[0][1]


async def test_bot_kick_does_not_post_left(mock_context):
    mock_context.bot.id = 999
    with session_scope() as s:
        s.add(Chat(id=-100))
    # кикнул сам бот (from_user == бот) → не дублируем
    await actions.on_chat_member_update(
        _left_update(-100, 42, new="kicked", from_id=999), mock_context)
    mock_context.bot.send_message.assert_not_called()


async def test_promotion_does_not_post_left(mock_context):
    mock_context.bot.id = 999
    with session_scope() as s:
        s.add(Chat(id=-100))
    await actions.on_chat_member_update(
        _left_update(-100, 42, old="member", new="administrator"), mock_context)
    mock_context.bot.send_message.assert_not_called()
