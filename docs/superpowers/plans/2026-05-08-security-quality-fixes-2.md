# Security & Code Quality Fixes Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 5 security issues and 6 code quality issues found by the security and code-quality reviewers.

**Architecture:** All fixes are in-place edits to existing files; no new modules. Each task is independently testable. The most invasive change (Markdown injection fix, Task 7) adds a helper and updates mention_markdown — all existing caller signatures stay the same.

**Tech Stack:** Python 3.9, python-telegram-bot v20+, SQLAlchemy, pytest-asyncio, SQLite (tests).

---

## File Map

| File | Changes |
|------|---------|
| `wachter/actions.py` | Tasks 1–5, 7 |
| `wachter/bot.py` | Task 6 |
| `tests/test_unit.py` | Tasks 1, 4, 5, 7 |
| `tests/test_handlers.py` | Tasks 2, 3, 5, 6, 7 |

---

### Task 1: Fix `on_kick_timeout` null check + `on_notify_timeout` await outside `session_scope`

**Issues fixed:** Code Quality 1 (kick null check), Code Quality 3 (session held open during HTTP call)

**Files:**
- Modify: `wachter/actions.py` -- `on_kick_timeout` (~line 376), `on_notify_timeout` (~line 341)
- Test: `tests/test_unit.py`

- [ ] **Step 1: Write failing tests**

Add at the bottom of `tests/test_unit.py`:

```python
class TestKickTimeoutChatNone:
    async def test_no_ban_when_chat_deleted(self):
        """When the chat row is gone, kick job must exit silently without calling ban."""
        from unittest.mock import AsyncMock, MagicMock, patch
        import actions

        job_data = {"chat_id": -100, "user_id": 42, "message_id": 1}
        context = MagicMock()
        context.job = MagicMock()
        context.job.data = job_data
        context.bot.delete_message = AsyncMock()
        context.bot.ban_chat_member = AsyncMock()
        context.bot.send_message = AsyncMock()

        with patch("actions.session_scope") as mock_scope:
            mock_sess = MagicMock()
            mock_sess.query.return_value.filter.return_value.first.return_value = None
            mock_scope.return_value.__enter__.return_value = mock_sess
            await actions.on_kick_timeout(context)

        context.bot.ban_chat_member.assert_not_called()
        context.bot.send_message.assert_not_called()


class TestNotifyTimeoutSessionClosed:
    async def test_notify_sent_after_session_closes(self):
        """mention_markdown must be awaited after session_scope exits."""
        from unittest.mock import AsyncMock, MagicMock, patch
        import actions

        job_data = {"chat_id": -100, "user_id": 42}
        context = MagicMock()
        context.job = MagicMock()
        context.job.data = job_data
        context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
        context.job_queue.run_once = MagicMock()

        mock_chat = MagicMock()
        mock_chat.notify_delta = 5
        mock_chat.notify_message = r"%USER\_MENTION%, представьтесь."

        session_was_closed = []

        class FakeScope:
            def __enter__(self):
                return self._sess
            def __exit__(self, *a):
                session_was_closed.append(True)
                return False
            _sess = MagicMock()

        fake_scope = FakeScope()
        fake_scope._sess.query.return_value.filter.return_value.first.return_value = mock_chat

        async def spy_mention(bot, chat_id, user_id, text):
            assert session_was_closed, "mention_markdown called while session still open"
            return "mention text"

        with patch("actions.session_scope", return_value=fake_scope), \
             patch("actions.mention_markdown", side_effect=spy_mention):
            await actions.on_notify_timeout(context)

        context.bot.send_message.assert_called_once()
```

- [ ] **Step 2: Run to confirm failures**

```
pipenv run pytest tests/test_unit.py::TestKickTimeoutChatNone tests/test_unit.py::TestNotifyTimeoutSessionClosed -v
```

Expected: 2 FAILED.

- [ ] **Step 3: Fix `on_kick_timeout` -- add null check**

In `wachter/actions.py`, inside `on_kick_timeout`, after the line `chat = sess.query(Chat).filter(Chat.id == data["chat_id"]).first()`, add:

```python
            if chat is None:
                return
```

- [ ] **Step 4: Fix `on_notify_timeout` -- move await outside session_scope**

Replace the entire body of `on_notify_timeout`:

```python
async def on_notify_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == data["chat_id"]).first()
        if chat is None:
            return
        notify_delta = chat.notify_delta
        notify_template = chat.notify_message
    msg_markdown = await mention_markdown(
        context.bot, data["chat_id"], data["user_id"], notify_template
    )
    message = await context.bot.send_message(
        data["chat_id"], text=msg_markdown, parse_mode=ParseMode.MARKDOWN
    )
    context.job_queue.run_once(
        delete_message,
        notify_delta * 60,
        data={"chat_id": data["chat_id"], "message_id": message.message_id},
    )
```

- [ ] **Step 5: Run tests**

```
pipenv run pytest tests/test_unit.py::TestKickTimeoutChatNone tests/test_unit.py::TestNotifyTimeoutSessionClosed -v
```

Expected: 2 PASSED.

- [ ] **Step 6: Run full suite**

```
pipenv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add wachter/actions.py tests/test_unit.py
git commit -m "fix: guard on_kick_timeout against None chat; move on_notify_timeout await outside session"
```

---

### Task 2: Fix `on_new_chat_member` -- passively-collected users bypass kick timer

**Issue fixed:** Code Quality 2

**Files:**
- Modify: `wachter/actions.py` -- `on_new_chat_member` (~line 298)
- Test: `tests/test_handlers.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_handlers.py`:

```python
class TestNewChatMemberPassiveUser:
    async def test_passive_user_gets_kick_timer_on_rejoin(self, mock_context):
        """A user with whois=None (passively collected) must get a kick timer on rejoin,
        not the known-user fast-path that skips scheduling."""
        from actions import on_new_chat_member
        from helpers import make_update

        update = make_update(chat_id=-100)
        update.message = MagicMock()
        update.message.chat_id = -100
        member = MagicMock()
        member.id = 77
        member.is_bot = False
        member.username = "passive"
        member.first_name = "Passive"
        member.last_name = None
        member.name = "Passive"
        update.message.new_chat_members = [member]
        update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=5))

        db_user = MagicMock()
        db_user.whois = None  # passively collected

        mock_chat = MagicMock()
        mock_chat.on_new_chat_member_message = r"Привет %USER\_MENTION%!"
        mock_chat.on_known_new_chat_member_message = "С возвращением"
        mock_chat.kick_timeout = 10
        mock_chat.notify_delta = 0

        mock_sess = MagicMock()
        mock_sess.query.return_value.filter.return_value.first.side_effect = [db_user]

        with patch("actions.session_scope") as mock_scope, \
             patch("actions._get_or_create_chat", return_value=mock_chat), \
             patch("actions.cancel_kick_jobs", new=AsyncMock(return_value=False)), \
             patch("actions.mention_markdown", new=AsyncMock(return_value="mention")):
            mock_scope.return_value.__enter__.return_value = mock_sess
            await on_new_chat_member(update, mock_context)

        for call in update.message.reply_text.call_args_list:
            assert "С возвращением" not in str(call), "passive user got known_message"
        mock_context.job_queue.run_once.assert_called()
```

- [ ] **Step 2: Run to confirm failure**

```
pipenv run pytest tests/test_handlers.py::TestNewChatMemberPassiveUser -v
```

Expected: FAILED.

- [ ] **Step 3: Implement fix**

In `wachter/actions.py`, inside `on_new_chat_member`, replace:

```python
            user_found = db_user is not None
```

With:

```python
            user_found = db_user is not None and db_user.whois is not None
```

- [ ] **Step 4: Run test**

```
pipenv run pytest tests/test_handlers.py::TestNewChatMemberPassiveUser -v
```

Expected: PASSED.

- [ ] **Step 5: Run full suite**

```
pipenv run pytest -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add wachter/actions.py tests/test_handlers.py
git commit -m "fix: passively-collected users (whois=None) now get kick timer on rejoin"
```

---

### Task 3: Fix `on_left_chat_member` missing `cancel_kick_jobs` + `assert` to `if` in settings input

**Issues fixed:** Code Quality 5 (missing cancel), Code Quality 4 (assert validation)

**Files:**
- Modify: `wachter/actions.py` -- `on_left_chat_member` (~line 845), `_handle_settings_input` (~line 746)
- Test: `tests/test_handlers.py`, `tests/test_unit.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_handlers.py`:

```python
class TestLeftChatMemberCancelsJob:
    async def test_kick_job_cancelled_on_voluntary_leave(self, mock_context):
        """When a user leaves, any pending kick job must be cancelled immediately."""
        from actions import on_left_chat_member
        from helpers import make_update

        update = make_update(chat_id=-100)
        update.message = MagicMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100
        member = MagicMock()
        member.id = 42
        member.is_bot = False
        member.name = "Alice"
        member.mention_markdown.return_value = "[Alice](tg://user?id=42)"
        update.message.left_chat_member = member
        update.message.reply_text = AsyncMock()

        mock_chat = MagicMock()
        mock_chat.on_left_chat_member_message = r"%USER\_MENTION% покинул чат"

        with patch("actions.session_scope") as mock_scope, \
             patch("actions.cancel_kick_jobs", new=AsyncMock(return_value=True)) as mock_cancel:
            mock_scope.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = mock_chat
            await on_left_chat_member(update, mock_context)

        mock_cancel.assert_called_once_with(mock_context.bot, mock_context.job_queue, -100, 42)
```

Add to `tests/test_unit.py`:

```python
class TestSettingsInputValidation:
    async def test_negative_kick_timeout_rejected(self, mock_context):
        """Negative numeric value must be rejected even when Python runs with -O."""
        from actions import _handle_settings_input
        from helpers import make_update
        from constants import Actions

        update = make_update(chat_id=42)
        update.effective_message.text = "-5"
        mock_context.user_data = {"action": Actions.set_kick_timeout, "chat_id": -100}

        with patch("actions.authorize_user", new=AsyncMock(return_value=True)), \
             patch("actions._save_chat_setting") as mock_save:
            await _handle_settings_input(update, mock_context)

        update.effective_message.reply_text.assert_called_once()
        mock_save.assert_not_called()

    async def test_zero_kick_timeout_accepted(self, mock_context):
        """Zero is a valid kick_timeout (disabled)."""
        from actions import _handle_settings_input
        from helpers import make_update
        from constants import Actions

        update = make_update(chat_id=42)
        update.effective_message.text = "0"
        mock_context.user_data = {"action": Actions.set_kick_timeout, "chat_id": -100}

        with patch("actions.authorize_user", new=AsyncMock(return_value=True)), \
             patch("actions._save_chat_setting") as mock_save:
            await _handle_settings_input(update, mock_context)

        mock_save.assert_called_once_with(-100, "kick_timeout", 0)
```

- [ ] **Step 2: Run to confirm failures**

```
pipenv run pytest tests/test_handlers.py::TestLeftChatMemberCancelsJob tests/test_unit.py::TestSettingsInputValidation -v
```

Expected: 3 FAILED.

- [ ] **Step 3: Fix `on_left_chat_member` -- add `cancel_kick_jobs`**

In `wachter/actions.py`, inside `on_left_chat_member`, after the `if member.is_bot: return` check, add:

```python
    await cancel_kick_jobs(context.bot, context.job_queue, update.effective_chat.id, member.id)
```

- [ ] **Step 4: Fix `assert` to `if` in `_handle_settings_input`**

Replace:

```python
        try:
            value = int(message.text)
            assert validator(value)
        except Exception:
            await message.reply_text(error_msg)
            return
```

With:

```python
        try:
            value = int(message.text)
        except (ValueError, TypeError):
            await message.reply_text(error_msg)
            return
        if not validator(value):
            await message.reply_text(error_msg)
            return
```

- [ ] **Step 5: Run tests**

```
pipenv run pytest tests/test_handlers.py::TestLeftChatMemberCancelsJob tests/test_unit.py::TestSettingsInputValidation -v
```

Expected: 3 PASSED.

- [ ] **Step 6: Run full suite**

```
pipenv run pytest -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add wachter/actions.py tests/test_handlers.py tests/test_unit.py
git commit -m "fix: cancel kick jobs on voluntary leave; replace assert with explicit if for settings validation"
```

---

### Task 4: Fix `_handle_group_message` TOCTOU + `_whois_nonadmin_attempts` key per (user, chat)

**Issues fixed:** Code Quality 6 (TOCTOU), Security 5 (whois counter cross-chat leak)

**Files:**
- Modify: `wachter/actions.py` -- `_handle_group_message`, `_whois_nonadmin_attempts`, `_handle_whois_spam`, `on_whois_command`
- Test: `tests/test_unit.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_unit.py`:

```python
class TestWhoisSpamPerChat:
    async def test_attempts_isolated_by_chat(self):
        """Counter in chat A must not bleed into chat B."""
        from unittest.mock import AsyncMock
        import time
        import actions
        from helpers import make_message

        actions._whois_nonadmin_attempts = {}
        uid = 55555

        msg_a = make_message(user_id=uid)
        msg_a.chat_id = -100
        msg_a.reply_text = AsyncMock()

        for _ in range(4):
            await actions._handle_whois_spam(msg_a, -100)

        msg_b = make_message(user_id=uid)
        msg_b.chat_id = -200
        msg_b.reply_text = AsyncMock()

        await actions._handle_whois_spam(msg_b, -200)
        msg_b.reply_text.assert_not_called()
        actions._whois_nonadmin_attempts = {}


class TestGroupMessageTOCTOU:
    async def test_reminder_shown_even_when_user_already_exists(self, mock_context):
        """When passive-collection hits a duplicate, the whois reminder must still fire."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from helpers import make_update
        import actions

        update = make_update(chat_id=-100, user_id=42, text="hello")
        update.effective_message.caption = None

        mock_context.job_queue.get_jobs_by_name.side_effect = (
            lambda name: [MagicMock()] if "kick_-100_42" in name else []
        )

        mock_chat = MagicMock()
        mock_chat.on_whois_reminder_message = r"%USER\_MENTION%, представься."
        mock_chat.min_whois_length = 20

        with patch("actions.is_new_user", return_value=True), \
             patch("actions.filter_message", return_value=False), \
             patch("actions.is_chat_filters_new_users", return_value=False), \
             patch("actions.authorize_user", new=AsyncMock(return_value=False)), \
             patch("actions.session_scope") as mock_scope, \
             patch("actions.mention_markdown", new=AsyncMock(return_value="reminder")):
            mock_sess = MagicMock()
            mock_sess.query.return_value.filter.return_value.first.side_effect = [
                mock_chat,    # _get_or_create_chat
                MagicMock(),  # duplicate check: user already exists
                mock_chat,    # reminder lookup
            ]
            mock_scope.return_value.__enter__.return_value = mock_sess
            await actions._handle_group_message(update, mock_context)

        update.effective_message.reply_text.assert_called()
```

- [ ] **Step 2: Run to confirm failures**

```
pipenv run pytest tests/test_unit.py::TestWhoisSpamPerChat tests/test_unit.py::TestGroupMessageTOCTOU -v
```

Expected: FAILED.

- [ ] **Step 3: Change `_whois_nonadmin_attempts` key to tuple**

In `wachter/actions.py`:

Change the type annotation:

```python
_whois_nonadmin_attempts: dict[tuple[int, int], tuple[int, float]] = {}
```

Replace `_handle_whois_spam` function:

```python
async def _handle_whois_spam(message, chat_id):
    """Handles /whois from non-admins: silent for first 4, responds on 5th."""
    uid = message.from_user.id if message.from_user else 0
    key = (uid, chat_id)
    now = monotonic()

    for k in [k for k, (_, t) in _whois_nonadmin_attempts.items() if now - t > _WHOIS_TTL_SECONDS]:
        del _whois_nonadmin_attempts[k]

    prev_count, last_time = _whois_nonadmin_attempts.get(key, (0, now))
    if now - last_time > _WHOIS_TTL_SECONDS:
        prev_count = 0

    count = prev_count + 1
    _whois_nonadmin_attempts[key] = (count, now)

    if count % _WHOIS_SPAM_THRESHOLD == 0:
        idx = (count // _WHOIS_SPAM_THRESHOLD - 1) % len(_WHOIS_SPAM_RESPONSES)
        await message.reply_text(_WHOIS_SPAM_RESPONSES[idx])
```

Update call site in `on_whois_command` (replace the `await _handle_whois_spam(message)` line):

```python
        await _handle_whois_spam(message, chat_id)
```

- [ ] **Step 4: Fix `_handle_group_message` TOCTOU**

In `wachter/actions.py`, replace the entire passive-collection block:

```python
    # Passive collection: new user, not filtered -- record and nudge.
    nudge_template = None
    min_len_str = "20"
    if user_is_new and not should_filter:
        try:
            with session_scope() as sess:
                chat_obj = _get_or_create_chat(sess, chat_id)
                nudge_template = chat_obj.on_existing_member_whois_request
                min_len_str = str(chat_obj.min_whois_length)
                tg = message.from_user
                existing = sess.query(User).filter(
                    User.user_id == user_id, User.chat_id == chat_id
                ).first()
                if existing is None:
                    sess.add(User(
                        user_id=user_id, chat_id=chat_id, whois=None,
                        username=tg.username,
                        first_name=tg.first_name,
                        last_name=tg.last_name,
                    ))
                else:
                    nudge_template = None  # already known; skip nudge but continue to reminder
        except Exception:
            logger.warning("passive collection insert failed for user %s in chat %s", user_id, chat_id)
            nudge_template = None
        if nudge_template is not None:
            nudge = await mention_markdown(context.bot, chat_id, user_id, nudge_template)
            nudge = _replace_min_length(nudge, min_len_str)
            await message.reply_text(nudge, parse_mode=ParseMode.MARKDOWN)
```

- [ ] **Step 5: Update existing TestWhoisSpamEviction tests**

In `tests/test_unit.py`, update both `TestWhoisSpamEviction` tests to pass `chat_id` and use tuple keys:

`test_stale_entries_evicted_on_next_call`:
```python
        stale_uid = 88888
        stale_key = (stale_uid, -100)
        stale_time = time.monotonic() - actions._WHOIS_TTL_SECONDS - 10
        actions._whois_nonadmin_attempts[stale_key] = (3, stale_time)

        msg = make_message(user_id=77777)
        await actions._handle_whois_spam(msg, -100)

        assert stale_key not in actions._whois_nonadmin_attempts
```

`test_fresh_entry_not_evicted`:
```python
        fresh_uid = 66666
        fresh_key = (fresh_uid, -100)
        actions._whois_nonadmin_attempts[fresh_key] = (2, time.monotonic())

        msg = make_message(user_id=77777)
        await actions._handle_whois_spam(msg, -100)

        assert fresh_key in actions._whois_nonadmin_attempts
        del actions._whois_nonadmin_attempts[fresh_key]
```

- [ ] **Step 6: Run all affected tests**

```
pipenv run pytest tests/test_unit.py::TestWhoisSpamPerChat tests/test_unit.py::TestGroupMessageTOCTOU tests/test_unit.py::TestWhoisSpamEviction -v
```

Expected: all PASSED.

- [ ] **Step 7: Run full suite**

```
pipenv run pytest -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add wachter/actions.py tests/test_unit.py
git commit -m "fix: isolate whois spam counter per (user_id, chat_id); fix TOCTOU early-return in group message handler"
```

---

### Task 5: Fix `_save_chat_setting` column allowlist + `get_current_settings` without Markdown

**Issues fixed:** Security 4 (allowlist), Security 2 (ParseMode.MARKDOWN in settings display)

**Files:**
- Modify: `wachter/actions.py` -- `_save_chat_setting`, `on_button_click` get_current_settings block
- Test: `tests/test_unit.py`, `tests/test_handlers.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_unit.py`:

```python
class TestSaveChatSettingAllowlist:
    def test_valid_column_does_not_raise(self):
        from actions import _save_chat_setting
        with patch("actions.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = MagicMock()
            _save_chat_setting(-100, "kick_timeout", 5)

    def test_invalid_column_raises(self):
        from actions import _save_chat_setting
        import pytest
        with patch("actions.session_scope"):
            with pytest.raises(AssertionError):
                _save_chat_setting(-100, "id", 999)

    def test_another_invalid_column_raises(self):
        from actions import _save_chat_setting
        import pytest
        with patch("actions.session_scope"):
            with pytest.raises(AssertionError):
                _save_chat_setting(-100, "__class__", None)
```

Add to `tests/test_handlers.py`:

```python
class TestGetCurrentSettingsNoMarkdown:
    async def test_settings_rendered_without_markdown_parse_mode(self, admin_context):
        """get_current_settings must not render with ParseMode.MARKDOWN."""
        from actions import on_button_click
        from constants import Actions
        import json
        from telegram.constants import ParseMode

        update = MagicMock()
        query = MagicMock()
        query.data = json.dumps({"action": int(Actions.get_current_settings), "chat_id": -100})
        query.message.chat.type = "private"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query

        mock_chat = MagicMock()
        mock_chat.__dict__ = {
            "id": -100, "kick_timeout": 0, "notify_delta": 10,
            "on_new_chat_member_message": "Привет",
            "on_known_new_chat_member_message": "Снова",
            "on_introduce_message": "OK",
            "notify_message": "Представься",
            "on_kick_message": "Пока",
            "on_left_chat_member_message": "Ушёл",
            "on_whois_reminder_message": "Напиши",
            "on_filtered_message": "Бан",
            "min_whois_length": 20, "ban_duration": 1,
            "regex_filter": None, "filter_only_new_users": False,
            "on_existing_member_whois_request": "Представься",
        }

        with patch("actions.session_scope") as mock_scope, \
             patch("actions._get_or_create_chat", return_value=mock_chat):
            mock_scope.return_value.__enter__.return_value = MagicMock()
            await on_button_click(update, admin_context)

        call_kwargs = query.edit_message_text.call_args[1]
        assert call_kwargs.get("parse_mode") != ParseMode.MARKDOWN
```

- [ ] **Step 2: Run to confirm failures**

```
pipenv run pytest tests/test_unit.py::TestSaveChatSettingAllowlist tests/test_handlers.py::TestGetCurrentSettingsNoMarkdown -v
```

Expected: 4 FAILED.

- [ ] **Step 3: Add `_ALLOWED_CHAT_COLUMNS` and fix `_save_chat_setting`**

In `wachter/actions.py`, after `_TEXT_SETTINGS` dict, add:

```python
_ALLOWED_CHAT_COLUMNS: frozenset[str] = (
    frozenset(_TEXT_SETTINGS.values())
    | {col for col, _, _ in _NUMERIC_SETTINGS.values()}
    | {"regex_filter", "filter_only_new_users"}
)
```

Update `_save_chat_setting`:

```python
def _save_chat_setting(chat_id, column, value):
    assert column in _ALLOWED_CHAT_COLUMNS, f"Attempted write to disallowed column: {column!r}"
    with session_scope() as sess:
        chat = _get_or_create_chat(sess, chat_id)
        setattr(chat, column, value)
```

- [ ] **Step 4: Remove `parse_mode=ParseMode.MARKDOWN` from `get_current_settings`**

In `wachter/actions.py`, inside `on_button_click`, find the `get_current_settings` block. Change:

```python
            await query.edit_message_text(
                text=settings_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_back_keyboard(chat_id),
            )
```

To:

```python
            await query.edit_message_text(
                text=settings_text,
                reply_markup=_back_keyboard(chat_id),
            )
```

- [ ] **Step 5: Run tests**

```
pipenv run pytest tests/test_unit.py::TestSaveChatSettingAllowlist tests/test_handlers.py::TestGetCurrentSettingsNoMarkdown -v
```

Expected: 4 PASSED.

- [ ] **Step 6: Run full suite**

```
pipenv run pytest -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add wachter/actions.py tests/test_unit.py tests/test_handlers.py
git commit -m "fix: add column allowlist to _save_chat_setting; render settings without ParseMode.MARKDOWN"
```

---

### Task 6: Remove persistence (eliminates deserialization attack vector)

**Issue fixed:** Security 3

**Files:**
- Modify: `wachter/bot.py`
- Test: `tests/test_handlers.py`

- [ ] **Step 1: Write smoke test**

Add to `tests/test_handlers.py`:

```python
class TestBotNoPersistence:
    def test_persistence_not_in_bot_source(self):
        """bot.py must not use PicklePersistence or any file-based persistence."""
        import pathlib
        source = pathlib.Path("wachter/bot.py").read_text()
        assert "Persistence" not in source, "File-based persistence must be removed from bot.py"
```

- [ ] **Step 2: Run to confirm failure**

```
pipenv run pytest tests/test_handlers.py::TestBotNoPersistence -v
```

Expected: FAILED.

- [ ] **Step 3: Rewrite `bot.py` without persistence**

Replace `wachter/bot.py` with:

```python
import os
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from custom_filters import filter_bot_added
import actions


def main():
    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_TOKEN"])
        .build()
    )

    app.add_error_handler(actions.on_error)
    app.add_handler(CommandHandler("help", actions.on_help_command))

    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS & filter_bot_added,
        actions.on_new_chat_member,
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.LEFT_CHAT_MEMBER,
        actions.on_left_chat_member,
    ))
    app.add_handler(MessageHandler(
        filters.Entity("hashtag"),
        actions.on_hashtag_message,
    ))
    app.add_handler(MessageHandler(filters.FORWARDED, actions.on_forward))
    app.add_handler(MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & filters.Entity("hashtag"),
        actions.on_edited_message,
    ))

    app.add_handler(CommandHandler("start", actions.on_start_command))
    app.add_handler(CommandHandler("skip", actions.on_skip_command))
    app.add_handler(CommandHandler("approve", actions.on_approve_command))
    app.add_handler(CommandHandler("whois", actions.on_whois_command))
    app.add_handler(CallbackQueryHandler(actions.on_button_click))
    app.add_handler(MessageHandler(
        filters.TEXT | filters.CAPTION,
        actions.on_message,
    ))

    app.run_polling()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test**

```
pipenv run pytest tests/test_handlers.py::TestBotNoPersistence -v
```

Expected: PASSED.

- [ ] **Step 5: Run full suite**

```
pipenv run pytest -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add wachter/bot.py tests/test_handlers.py
git commit -m "fix: remove file-based persistence -- eliminates deserialization attack vector; admin state is transient"
```

---

### Task 7: Fix Markdown injection -- escape template text before rendering

**Issue fixed:** Security 1 (critical)

**Design:** Add `_apply_mention(template, mention)` helper that:
1. Normalises both `%USER_MENTION%` and `%USER\_MENTION%` placeholder forms
2. Splits the template on the placeholder
3. Escapes each literal text part with `escape_markdown(version=1)` (turns `[` into `\[`)
4. Joins with the pre-formatted mention string (not escaped -- it is already valid Markdown)

Templates are also saved via `message.text` (not `message.text_markdown`) to prevent Telegram-entity hyperlinks from being stored.

**Files:**
- Modify: `wachter/actions.py` -- add `_apply_mention`, update `mention_markdown`, `on_left_chat_member`, `_handle_settings_input`
- Test: `tests/test_unit.py`, `tests/test_handlers.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_unit.py`:

```python
class TestApplyMention:
    def test_placeholder_substituted(self):
        from actions import _apply_mention
        result = _apply_mention(r"%USER\_MENTION% привет", "[Alice](tg://user?id=1)")
        assert "[Alice](tg://user?id=1)" in result
        assert "%USER" not in result

    def test_link_injection_escaped(self):
        """A [phishing](url) in template must have its [ escaped to prevent link rendering."""
        from actions import _apply_mention
        result = _apply_mention(r"[click](https://evil.com) %USER\_MENTION%", "[Alice](tg://user?id=1)")
        assert "[click](https://evil.com)" not in result
        assert "\\[click]" in result or "\\[click" in result

    def test_plain_form_placeholder_accepted(self):
        """Admin may type %USER_MENTION% without the backslash."""
        from actions import _apply_mention
        result = _apply_mention("%USER_MENTION% hello", "[Alice](tg://user?id=1)")
        assert "[Alice](tg://user?id=1)" in result

    def test_no_placeholder_text_escaped(self):
        """Template with no placeholder: text still escaped, no mention injected."""
        from actions import _apply_mention
        result = _apply_mention("hello *world*", "")
        assert "*world*" not in result
        assert "\\*world\\*" in result


class TestMentionMarkdownEscaping:
    async def test_link_injection_prevented(self, mock_bot):
        """mention_markdown must escape Markdown link syntax in the template."""
        from actions import mention_markdown
        mock_bot.get_chat_member.return_value.user.name = "Alice"
        mock_bot.get_chat_member.return_value.user.mention_markdown.return_value = "[Alice](tg://user?id=1)"

        template = r"[evil](https://phishing.com) %USER\_MENTION%"
        result = await mention_markdown(mock_bot, -100, 1, template)

        assert "[Alice](tg://user?id=1)" in result
        assert "[evil](https://phishing.com)" not in result
```

Add to `tests/test_handlers.py`:

```python
class TestTemplateStoredAsPlainText:
    async def test_plain_text_not_markdown_entities_stored(self, admin_context):
        """_handle_settings_input must use message.text, not message.text_markdown,
        so Telegram-entity hyperlinks are not captured in stored templates."""
        from actions import _handle_settings_input
        from helpers import make_update
        from constants import Actions

        update = make_update(chat_id=42)
        update.effective_message.text = "Привет %USER_MENTION%"
        update.effective_message.text_markdown = "[Привет](https://evil.com) %USER_MENTION%"
        admin_context.user_data = {
            "action": Actions.set_on_new_chat_member_message_response,
            "chat_id": -100,
        }

        with patch("actions.authorize_user", new=AsyncMock(return_value=True)), \
             patch("actions._save_chat_setting") as mock_save:
            await _handle_settings_input(update, admin_context)

        mock_save.assert_called_once_with(-100, "on_new_chat_member_message", "Привет %USER_MENTION%")
```

- [ ] **Step 2: Run to confirm failures**

```
pipenv run pytest tests/test_unit.py::TestApplyMention tests/test_unit.py::TestMentionMarkdownEscaping tests/test_handlers.py::TestTemplateStoredAsPlainText -v
```

Expected: all FAILED.

- [ ] **Step 3: Add `_apply_mention` helper**

In `wachter/actions.py`, after `_replace_min_length`, add:

```python
def _apply_mention(template: str, mention: str) -> str:
    """Escape Markdown in template text, substituting the mention placeholder with a
    pre-formatted mention. Accepts both %USER_MENTION% and %USER\_MENTION% forms.
    The [ character in template literal text is escaped to \[ to prevent link injection."""
    normalized = template.replace("%USER_MENTION%", _MENTION_PLACEHOLDER)
    parts = normalized.split(_MENTION_PLACEHOLDER)
    return mention.join(escape_markdown(p, version=1) for p in parts)
```

- [ ] **Step 4: Update `mention_markdown` to use `_apply_mention`**

Replace the last line of `mention_markdown`:

```python
    return message.replace(_MENTION_PLACEHOLDER, user_mention_markdown)
```

With:

```python
    return _apply_mention(message, user_mention_markdown)
```

- [ ] **Step 5: Update `on_left_chat_member` to use `_apply_mention`**

In `on_left_chat_member`, replace:

```python
    user_mention = member.mention_markdown() if member.name else str(member.id)
    msg = template.replace(_MENTION_PLACEHOLDER, user_mention)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
```

With:

```python
    user_mention = member.mention_markdown() if member.name else str(member.id)
    msg = _apply_mention(template, user_mention)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
```

- [ ] **Step 6: Change template storage from `text_markdown` to `text`**

In `_handle_settings_input`, replace:

```python
    elif action in _TEXT_SETTINGS:
        _save_chat_setting(chat_id, _TEXT_SETTINGS[action], message.text_markdown)
```

With:

```python
    elif action in _TEXT_SETTINGS:
        _save_chat_setting(chat_id, _TEXT_SETTINGS[action], message.text)
```

And replace:

```python
    elif action == Actions.set_filter_only_new_users:
        _save_chat_setting(chat_id, "filter_only_new_users", message.text_markdown.lower() in ("true", "1"))
```

With:

```python
    elif action == Actions.set_filter_only_new_users:
        _save_chat_setting(chat_id, "filter_only_new_users", message.text.lower() in ("true", "1"))
```

- [ ] **Step 7: Run new tests**

```
pipenv run pytest tests/test_unit.py::TestApplyMention tests/test_unit.py::TestMentionMarkdownEscaping tests/test_handlers.py::TestTemplateStoredAsPlainText -v
```

Expected: all PASSED.

- [ ] **Step 8: Run full suite**

```
pipenv run pytest -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add wachter/actions.py tests/test_unit.py tests/test_handlers.py
git commit -m "fix: escape Markdown in bot message templates via _apply_mention; store templates as plain text"
```

---

## Self-Review

**Spec coverage:**

| Issue | Task | Covered |
|-------|------|---------|
| QA-1 on_kick_timeout null check | Task 1 | YES |
| QA-2 passive user bypass kick timer | Task 2 | YES |
| QA-3 await inside session_scope | Task 1 | YES |
| QA-4 assert -> if for validation | Task 3 | YES |
| QA-5 missing cancel_kick_jobs on leave | Task 3 | YES |
| QA-6 TOCTOU early return | Task 4 | YES |
| Sec-1 Markdown injection via templates | Task 7 | YES |
| Sec-2 get_current_settings ParseMode | Task 5 | YES |
| Sec-3 file-based persistence deserialization | Task 6 | YES |
| Sec-4 _save_chat_setting setattr allowlist | Task 5 | YES |
| Sec-5 whois counter cross-chat | Task 4 | YES |

All 11 issues covered. No placeholders, no TBD, no "similar to" references.
