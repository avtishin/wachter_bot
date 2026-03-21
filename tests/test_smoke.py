"""
Smoke-тесты: проверяем что всё импортируется, хендлеры async,
константы на месте, хардкодов нет.
"""
import asyncio


def test_actions_imports():
    import actions
    expected = [
        "on_help_command", "on_new_chat_member", "on_hashtag_message",
        "on_skip_command", "on_approve_command", "on_button_click",
        "on_message", "on_whois_command", "on_forward", "on_error",
        "cancel_kick_jobs", "authorize_user", "mention_markdown",
    ]
    for name in expected:
        assert hasattr(actions, name), f"actions.{name} не найден"


def test_all_handlers_are_coroutines():
    import actions
    handlers = [
        "on_help_command", "on_new_chat_member", "on_hashtag_message",
        "on_skip_command", "on_approve_command", "on_button_click",
        "on_message", "on_whois_command", "on_forward", "on_error",
    ]
    for name in handlers:
        fn = getattr(actions, name)
        assert asyncio.iscoroutinefunction(fn), f"{name} должен быть async def"


def test_job_callbacks_are_coroutines():
    import actions
    for name in ["on_kick_timeout", "on_notify_timeout", "delete_message"]:
        fn = getattr(actions, name)
        assert asyncio.iscoroutinefunction(fn), f"{name} должен быть async def"


def test_helper_functions_are_coroutines():
    import actions
    for name in ["authorize_user", "mention_markdown", "cancel_kick_jobs"]:
        fn = getattr(actions, name)
        assert asyncio.iscoroutinefunction(fn), f"{name} должен быть async def"


def test_constants_exist():
    import constants
    required = [
        "help_message", "min_whois_length", "notify_delta",
        "skip_on_new_chat_member_message", "on_filtered_message",
        "on_success_skip", "on_failed_skip", "on_set_new_message",
        "on_failed_kick_response", "Actions",
    ]
    for name in required:
        assert hasattr(constants, name), f"constants.{name} не найден"


def test_constants_values_are_sane():
    import constants
    assert constants.min_whois_length > 0
    assert constants.notify_delta > 0
    assert "%TIMEOUT%" in constants.help_message
    assert "%USER_MENTION%" in constants.help_message


def test_no_hardcoded_chat_ids():
    """RH_CHAT_ID должен быть удалён."""
    import constants
    assert not hasattr(constants, "RH_CHAT_ID")
    assert not hasattr(constants, "RH_kick_messages")


def test_actions_no_random_import():
    """random использовался только для RH_kick_messages — должен быть удалён."""
    import ast
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "wachter", "actions.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    imports = [
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    ]
    assert "random" not in imports


def test_bot_module_has_main():
    import bot
    assert hasattr(bot, "main")
    assert callable(bot.main)


def test_model_columns():
    from model import Chat, User
    chat_cols = {c.name for c in Chat.__table__.columns}
    assert {"id", "kick_timeout", "regex_filter", "filter_only_new_users",
            "on_new_chat_member_message", "on_kick_message",
            "notify_message", "on_introduce_message"} <= chat_cols

    user_cols = {c.name for c in User.__table__.columns}
    assert {"user_id", "chat_id", "whois"} <= user_cols


def test_default_welcome_message_has_timeout_placeholder():
    from model import Chat
    col = Chat.__table__.columns["on_new_chat_member_message"]
    assert "%TIMEOUT%" in col.default.arg
