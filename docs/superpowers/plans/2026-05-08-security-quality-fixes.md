# Security & Code Quality Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all security vulnerabilities and code quality issues found in security + code reviews: migration DAG branch, sess.merge data loss, ReDoS, Markdown injection, PTB persistence directory permissions, unbounded spam dict, missing auth check, blocking async DB calls, and hardcoded migration default.

**Architecture:** All fixes are isolated patches to existing files. No new modules. Tasks are ordered by severity (critical first). Tests use the existing pytest/SQLite infrastructure.

**Tech Stack:** Python 3.12, python-telegram-bot v20+, SQLAlchemy, Alembic, pytest, SQLite (tests), PostgreSQL (prod)

---

## Files Changed

| File | Tasks |
|------|-------|
| `migrations/versions/f0g1h2i3j4k5_merge_metadata_branches.py` | Task 1 (new) |
| `migrations/versions/a1b2c3d4e5f0_fix_whois_reminder_placeholder.py` | Task 10 (new) |
| `wachter/actions.py` | Tasks 2, 3, 4, 6, 7, 8, 9 |
| `wachter/bot.py` | Task 5 |
| `tests/test_handlers.py` | Tasks 2, 4, 6, 7 |
| `tests/test_unit.py` | Tasks 3, 9 |

---

## Task 1: Fix Migration DAG Branch

Two migrations share `down_revision = 'c3d4e5f6a7b8'`, creating two heads. `alembic upgrade head` fails on any fresh deployment.

**Files:**
- Create: `migrations/versions/f0g1h2i3j4k5_merge_metadata_branches.py`

- [ ] **Step 1: Verify the problem**

```bash
cd /Users/atishin/Yandex.Disk.localized/GitHub/wachter_bot
alembic heads
```

Expected: two lines — `d4e5f6a7b8c9` and `e5f6a7b8c9d0` both listed as heads.

- [ ] **Step 2: Create the merge migration file**

Create `migrations/versions/f0g1h2i3j4k5_merge_metadata_branches.py`:

```python
"""merge d4e5 and e5f6 metadata branches

Revision ID: f0g1h2i3j4k5
Revises: d4e5f6a7b8c9, e5f6a7b8c9d0
Create Date: 2026-05-08 00:00:00.000000

"""
from alembic import op

revision = 'f0g1h2i3j4k5'
down_revision = ('d4e5f6a7b8c9', 'e5f6a7b8c9d0')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
```

- [ ] **Step 3: Verify single head**

```bash
alembic heads
```

Expected: single line — `f0g1h2i3j4k5`.

- [ ] **Step 4: Run tests**

```bash
pytest -v
```

Expected: 118 passed.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/f0g1h2i3j4k5_merge_metadata_branches.py
git commit -m "fix: merge migration DAG branch (d4e5 + e5f6 -> f0g1)"
```

---

## Task 2: Fix sess.merge(Chat) Data Loss in _handle_settings_input

`sess.merge(Chat(id=chat_id, kick_timeout=value))` creates a Chat object with all other columns as Python `None`. SQLAlchemy merge then writes those NULLs over every other setting, silently destroying the chat configuration.

**Files:**
- Modify: `wachter/actions.py` (lines 700-729, `_handle_settings_input`)
- Test: `tests/test_handlers.py`

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_handlers.py`:

```python
# ---------------------------------------------------------------------------
# Settings save regression: sess.merge(Chat) must not zero other columns
# ---------------------------------------------------------------------------

class TestSettingsSavePreservation:
    async def test_saving_kick_timeout_preserves_min_whois_length(self, admin_context):
        """Regression: old sess.merge(Chat(...)) reset all unset columns to None."""
        from model import Chat, session_scope
        from actions import _handle_settings_input
        from constants import Actions

        with session_scope() as sess:
            sess.add(Chat(id=-100, kick_timeout=30, min_whois_length=50))

        admin_context.user_data["action"] = Actions.set_kick_timeout
        admin_context.user_data["chat_id"] = -100
        update = make_update(chat_id=100, user_id=100, text="45")

        await _handle_settings_input(update, admin_context)

        with session_scope() as sess:
            saved = sess.query(Chat).filter(Chat.id == -100).first()
            assert saved.kick_timeout == 45
            assert saved.min_whois_length == 50  # must NOT be reset to default

    async def test_saving_text_setting_preserves_numeric_settings(self, admin_context):
        """Saving a text message template must not reset kick_timeout."""
        from model import Chat, session_scope
        from actions import _handle_settings_input
        from constants import Actions

        with session_scope() as sess:
            sess.add(Chat(id=-100, kick_timeout=60, on_introduce_message="Старое"))

        admin_context.user_data["action"] = Actions.set_on_successful_introducion_response
        admin_context.user_data["chat_id"] = -100
        update = make_update(chat_id=100, user_id=100, text="Новое")

        await _handle_settings_input(update, admin_context)

        with session_scope() as sess:
            saved = sess.query(Chat).filter(Chat.id == -100).first()
            assert saved.on_introduce_message == "Новое"
            assert saved.kick_timeout == 60  # must NOT be reset to default 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_handlers.py::TestSettingsSavePreservation -v
```

Expected: FAIL (merge code resets unset columns).

- [ ] **Step 3: Fix _handle_settings_input in actions.py**

Replace the four `sess.merge(Chat(id=chat_id, ...))` blocks (lines ~700-729) with `_get_or_create_chat` + `setattr`:

```python
    if action in _NUMERIC_SETTINGS:
        column, validator, error_msg = _NUMERIC_SETTINGS[action]
        try:
            value = int(message.text)
            assert validator(value)
        except Exception:
            await message.reply_text(error_msg)
            return
        with session_scope() as sess:
            chat = _get_or_create_chat(sess, chat_id)
            setattr(chat, column, value)
        saved = True

    elif action in _TEXT_SETTINGS:
        column = _TEXT_SETTINGS[action]
        with session_scope() as sess:
            chat = _get_or_create_chat(sess, chat_id)
            setattr(chat, column, message.text_markdown)
        saved = True

    elif action == Actions.set_filter_only_new_users:
        flag = message.text_markdown.lower() in ("true", "1")
        with session_scope() as sess:
            chat = _get_or_create_chat(sess, chat_id)
            chat.filter_only_new_users = flag
        saved = True

    elif action == Actions.set_regex_filter:
        regex = None if message.text_markdown == "%TURN_OFF%" else message.text
        with session_scope() as sess:
            chat = _get_or_create_chat(sess, chat_id)
            chat.regex_filter = regex
        saved = True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_handlers.py::TestSettingsSavePreservation -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 6: Commit**

```bash
git add wachter/actions.py tests/test_handlers.py
git commit -m "fix: replace sess.merge(Chat) with query+update in _handle_settings_input"
```

---

## Task 3: Fix ReDoS — Thread Timeout for re.search

Any chat admin can set `regex_filter` to a pathological pattern like `^(a+)+$`. One crafted message freezes the entire asyncio event loop for minutes.

**Files:**
- Modify: `wachter/actions.py`
- Test: `tests/test_unit.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# ReDoS protection
# ---------------------------------------------------------------------------

class TestSafeReSearch:
    def test_normal_pattern_matches(self):
        from actions import _safe_re_search
        assert _safe_re_search(r"крипт|invest", "купи крипту срочно")

    def test_normal_pattern_no_match(self):
        from actions import _safe_re_search
        assert not _safe_re_search(r"крипт|invest", "привет всем")

    def test_redos_pattern_times_out_and_returns_false(self):
        """Pathological ReDoS regex must not block for more than 1.5 seconds."""
        import time
        from actions import _safe_re_search
        start = time.monotonic()
        result = _safe_re_search(r"^(a+)+$", "a" * 28 + "b")
        elapsed = time.monotonic() - start
        assert not result
        assert elapsed < 1.5, f"ReDoS regex took {elapsed:.2f}s — timeout not working"

    def test_invalid_regex_returns_false(self):
        from actions import _safe_re_search
        assert not _safe_re_search(r"(unclosed", "any text")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_unit.py::TestSafeReSearch -v
```

Expected: FAIL (`_safe_re_search` not defined).

- [ ] **Step 3: Add _safe_re_search and update filter_message**

Add `import threading` to the imports in `actions.py` (after `import re`).

Add after `_WHOIS_SPAM_RESPONSES` list (before `_SETTINGS_BUTTONS`):

```python
_REGEX_TIMEOUT = 0.5  # seconds; prevents ReDoS from blocking the event loop


def _safe_re_search(pattern, text):
    """Run re.search in a daemon thread with timeout to prevent ReDoS."""
    result = [False]

    def _run():
        try:
            result[0] = bool(re.search(pattern, text))
        except re.error:
            result[0] = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(_REGEX_TIMEOUT)
    if thread.is_alive():
        logger.warning(f"Regex search timed out after {_REGEX_TIMEOUT}s for pattern {pattern!r}")
        return False
    return result[0]
```

In `filter_message`, replace:

```python
        try:
            return re.search(chat.regex_filter, message_text)
        except re.error:
            logger.warning(f"Invalid regex filter for chat {chat_id}: {chat.regex_filter!r}")
            return False
```

with:

```python
        return _safe_re_search(chat.regex_filter, message_text)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_unit.py::TestSafeReSearch -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 6: Commit**

```bash
git add wachter/actions.py tests/test_unit.py
git commit -m "fix: add thread timeout for re.search to prevent ReDoS"
```

---

## Task 4: Fix Markdown Injection in /whois Command

`user.whois` is inserted raw into a Markdown-mode reply. A user whose whois contains `*`, `` ` ``, `_`, or `[text](url)` can corrupt admin output or craft phishing links.

**Files:**
- Modify: `wachter/actions.py` (`on_whois_command`)
- Test: `tests/test_handlers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_handlers.py`:

```python
# ---------------------------------------------------------------------------
# /whois Markdown escape
# ---------------------------------------------------------------------------

class TestWhoisMarkdownEscape:
    async def test_whois_with_markdown_chars_is_escaped(self, admin_context):
        """Whois text containing Markdown syntax must be escaped before display."""
        from model import User, session_scope
        from actions import on_whois_command

        with session_scope() as sess:
            sess.add(User(user_id=42, chat_id=-100, whois="*bold* `code` _italic_"))

        update = make_update(chat_id=-100, user_id=100)
        update.effective_chat.id = -100
        update.message.reply_to_message = make_message(user_id=42)
        admin_context.args = []

        await on_whois_command(update, admin_context)

        call_args = update.effective_message.reply_text.call_args
        text = call_args[0][0]
        assert "*bold*" not in text
        assert "`code`" not in text
        assert "_italic_" not in text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_handlers.py::TestWhoisMarkdownEscape -v
```

Expected: FAIL (raw Markdown chars present).

- [ ] **Step 3: Fix on_whois_command**

Add to imports at the top of `actions.py`:

```python
from telegram.helpers import escape_markdown
```

In `on_whois_command`, replace:

```python
    whois_display = whois_text if whois_text is not None else "не представился"
```

with:

```python
    whois_display = escape_markdown(whois_text, version=1) if whois_text is not None else "не представился"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_handlers.py::TestWhoisMarkdownEscape -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 6: Commit**

```bash
git add wachter/actions.py tests/test_handlers.py
git commit -m "fix: escape user whois text before inserting into Markdown reply"
```

---

## Task 5: Fix PTB Persistence Directory Permissions

`Path("data").mkdir(exist_ok=True)` creates the directory with the process umask (typically world-readable). The persistence file containing admin session state should be readable only by the process owner.

**Files:**
- Modify: `wachter/bot.py`

No test needed — OS-level permission enforcement cannot be reliably tested in CI. The change is 3 lines.

- [ ] **Step 1: Fix bot.py**

Add `import os` to imports.

Replace:

```python
    Path("data").mkdir(exist_ok=True)

    persistence = PicklePersistence(
        filepath=os.environ.get("PERSISTENCE_PATH", "data/bot_persistence")
    )
```

with:

```python
    persist_path = os.environ.get("PERSISTENCE_PATH", "data/bot_persistence")
    persist_dir = os.path.dirname(persist_path) or "data"
    os.makedirs(persist_dir, exist_ok=True)
    os.chmod(persist_dir, 0o700)

    persistence = PicklePersistence(filepath=persist_path)
```

The `Path` import can be removed if it's no longer used elsewhere in `bot.py`.

- [ ] **Step 2: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 3: Commit**

```bash
git add wachter/bot.py
git commit -m "fix: restrict PTB persistence data directory permissions to 0o700"
```

---

## Task 6: Fix Unbounded _whois_nonadmin_attempts Dict

Entries are only reset on re-access, never evicted. With many unique users calling /whois once and leaving, the dict grows forever.

**Files:**
- Modify: `wachter/actions.py` (`_handle_whois_spam`)
- Test: `tests/test_unit.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# _handle_whois_spam stale entry eviction
# ---------------------------------------------------------------------------

class TestWhoisSpamEviction:
    async def test_stale_entries_evicted_on_next_call(self):
        """Entries older than TTL must be removed from the dict."""
        import time
        import actions
        from helpers import make_message

        stale_uid = 88888
        stale_time = time.monotonic() - actions._WHOIS_TTL_SECONDS - 10
        actions._whois_nonadmin_attempts[stale_uid] = (3, stale_time)

        msg = make_message(user_id=77777)
        await actions._handle_whois_spam(msg)

        assert stale_uid not in actions._whois_nonadmin_attempts

    async def test_fresh_entry_not_evicted(self):
        """Entries within TTL window must not be removed."""
        import time
        import actions
        from helpers import make_message

        fresh_uid = 66666
        actions._whois_nonadmin_attempts[fresh_uid] = (2, time.monotonic())

        msg = make_message(user_id=77777)
        await actions._handle_whois_spam(msg)

        assert fresh_uid in actions._whois_nonadmin_attempts
        del actions._whois_nonadmin_attempts[fresh_uid]  # cleanup
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_unit.py::TestWhoisSpamEviction -v
```

Expected: FAIL (stale entry not removed).

- [ ] **Step 3: Add eviction to _handle_whois_spam**

Replace `_handle_whois_spam` in `actions.py`:

```python
async def _handle_whois_spam(message):
    """Обрабатывает /whois от не-админа: молчим первые 4 раза, на 5-й — снапает."""
    uid = message.from_user.id if message.from_user else 0
    now = monotonic()

    # Evict idle entries to prevent unbounded dict growth
    stale = [k for k, (_, t) in _whois_nonadmin_attempts.items() if now - t > _WHOIS_TTL_SECONDS]
    for k in stale:
        del _whois_nonadmin_attempts[k]

    prev_count, last_time = _whois_nonadmin_attempts.get(uid, (0, now))
    if now - last_time > _WHOIS_TTL_SECONDS:
        prev_count = 0

    count = prev_count + 1
    _whois_nonadmin_attempts[uid] = (count, now)

    if count % _WHOIS_SPAM_THRESHOLD == 0:
        idx = (count // _WHOIS_SPAM_THRESHOLD - 1) % len(_WHOIS_SPAM_RESPONSES)
        await message.reply_text(_WHOIS_SPAM_RESPONSES[idx])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_unit.py::TestWhoisSpamEviction -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 6: Commit**

```bash
git add wachter/actions.py tests/test_unit.py
git commit -m "fix: evict stale entries from _whois_nonadmin_attempts to prevent memory growth"
```

---

## Task 7: Fix start_select_chat Auth — DM-Only Callbacks

`on_button_click` skips the auth check for `start_select_chat` (no `chat_id` in payload). Any user can trigger `_build_chat_list_keyboard`, which calls `bot.get_chat()` for all chats in the DB — information disclosure.

**Files:**
- Modify: `wachter/actions.py` (`on_button_click`)
- Test: `tests/test_handlers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_handlers.py`:

```python
# ---------------------------------------------------------------------------
# on_button_click DM-only guard
# ---------------------------------------------------------------------------

class TestButtonClickDMOnly:
    async def test_group_callback_is_silently_rejected(self, mock_context):
        """Callbacks from group chats must be ignored without side effects."""
        from actions import on_button_click

        query = MagicMock()
        query.from_user.id = 42
        query.data = '{"action": 1}'
        query.message = MagicMock()
        query.message.chat.type = "group"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        await on_button_click(update, mock_context)

        query.answer.assert_called_once()
        query.edit_message_text.assert_not_called()
        query.edit_message_reply_markup.assert_not_called()

    async def test_private_callback_proceeds(self, mock_context):
        """Callbacks from DM chats must be processed normally."""
        from actions import on_button_click

        query = MagicMock()
        query.from_user.id = 42
        query.data = '{"action": 1}'  # start_select_chat
        query.message = MagicMock()
        query.message.chat.type = "private"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        # No DB entries -> keyboard empty -> "У вас нет доступных чатов."
        await on_button_click(update, mock_context)

        query.answer.assert_called_once()
        assert query.edit_message_text.called or query.edit_message_reply_markup.called
```

- [ ] **Step 2: Run tests to verify group test fails**

```bash
pytest tests/test_handlers.py::TestButtonClickDMOnly -v
```

Expected: `test_group_callback_is_silently_rejected` FAIL.

- [ ] **Step 3: Add DM guard to on_button_click**

After `await query.answer()` (around line 527), insert the guard before the action branches:

Replace:

```python
    await query.answer()

    if action == Actions.start_select_chat:
```

with:

```python
    await query.answer()

    # Settings menu is only valid in DMs
    if query.message.chat.type != "private":
        return

    if action == Actions.start_select_chat:
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_handlers.py::TestButtonClickDMOnly -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 6: Commit**

```bash
git add wachter/actions.py tests/test_handlers.py
git commit -m "fix: restrict settings callbacks to private chats only"
```

---

## Task 8: Document on_forward Intentional Design

`on_forward` bans all forwards when `regex_filter is not None`, without checking message content against the pattern. This is intentional (the regex acts as an "enable forward ban" toggle) but surprising. Add a comment.

**Files:**
- Modify: `wachter/actions.py` (`on_forward`)

- [ ] **Step 1: Add comment**

In `on_forward`, replace:

```python
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None or chat.regex_filter is None:
            return
```

with:

```python
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        # regex_filter acts as an "enable forward ban" toggle here — all forwards
        # are treated as spam when any filter is active, regardless of content.
        if chat is None or chat.regex_filter is None:
            return
```

- [ ] **Step 2: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 3: Commit**

```bash
git add wachter/actions.py
git commit -m "docs: clarify that on_forward uses regex_filter as an enable-ban toggle"
```

---

## Task 9: Fix Blocking DB Calls in Async Handler

`is_new_user()`, `filter_message()`, and `is_chat_filters_new_users()` are synchronous SQLAlchemy functions called directly from `async def _handle_group_message`. In python-telegram-bot v20+, async handlers run on the event loop — blocking calls here delay all other updates.

Fix: wrap with `asyncio.get_running_loop().run_in_executor(None, ...)`.

**Files:**
- Modify: `wachter/actions.py` (`_handle_group_message`, imports)
- Test: `tests/test_unit.py`

- [ ] **Step 1: Write baseline tests (verify behavior before and after refactor)**

Add to `tests/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# _handle_group_message executor wrappers
# ---------------------------------------------------------------------------

class TestHandleGroupMessageAsync:
    async def test_new_user_triggers_passive_collection_reply(self, mock_context):
        """is_new_user=True with no filter match must trigger passive collection."""
        from unittest.mock import patch, MagicMock
        from actions import _handle_group_message
        from helpers import make_update

        update = make_update(chat_id=-100, user_id=42, text="hello")
        chat_mock = MagicMock()
        chat_mock.on_existing_member_whois_request = "Привет %USER\\_MENTION%"
        chat_mock.min_whois_length = 20

        with patch("actions.is_new_user", return_value=True), \
             patch("actions.filter_message", return_value=False), \
             patch("actions.is_chat_filters_new_users", return_value=False), \
             patch("actions.session_scope") as mock_sess:
            sess = mock_sess.return_value.__enter__.return_value
            sess.query.return_value.filter.return_value.first.return_value = chat_mock
            await _handle_group_message(update, mock_context)

        update.effective_message.reply_text.assert_called()

    async def test_known_user_no_reply_when_no_kick_job(self, mock_context):
        """Known user with no kick job and no filter match must produce no reply."""
        from unittest.mock import patch, MagicMock
        from actions import _handle_group_message
        from helpers import make_update

        update = make_update(chat_id=-100, user_id=42, text="hello")

        with patch("actions.is_new_user", return_value=False), \
             patch("actions.filter_message", return_value=False), \
             patch("actions.is_chat_filters_new_users", return_value=False), \
             patch("actions.session_scope") as mock_sess:
            sess = mock_sess.return_value.__enter__.return_value
            sess.query.return_value.filter.return_value.first.return_value = None
            await _handle_group_message(update, mock_context)

        update.effective_message.reply_text.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they pass (baseline)**

```bash
pytest tests/test_unit.py::TestHandleGroupMessageAsync -v
```

Expected: PASS.

- [ ] **Step 3: Add asyncio import and wrap DB calls**

Add `import asyncio` to the imports in `actions.py`.

In `_handle_group_message`, replace:

```python
    user_is_new = is_new_user(chat_id, user_id)

    should_filter = (
        not await authorize_user(context.bot, chat_id, user_id)
        and bool(filter_message(chat_id, message_text))
    )
    if should_filter and is_chat_filters_new_users(chat_id):
        should_filter = user_is_new
```

with:

```python
    loop = asyncio.get_running_loop()
    user_is_new = await loop.run_in_executor(None, is_new_user, chat_id, user_id)

    is_admin = await authorize_user(context.bot, chat_id, user_id)
    filter_result = await loop.run_in_executor(None, filter_message, chat_id, message_text)
    should_filter = not is_admin and bool(filter_result)
    if should_filter:
        filters_new_only = await loop.run_in_executor(None, is_chat_filters_new_users, chat_id)
        if filters_new_only:
            should_filter = user_is_new
```

- [ ] **Step 4: Run tests to verify they still pass**

```bash
pytest tests/test_unit.py::TestHandleGroupMessageAsync -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 6: Commit**

```bash
git add wachter/actions.py tests/test_unit.py
git commit -m "fix: run blocking DB calls in executor to avoid blocking the event loop"
```

---

## Task 10: Fix Migration c3d4 Hardcoded "20" Server Default

Migration `c3d4e5f6a7b8` added `on_whois_reminder_message` with `server_default` containing `"минимум 20 символов"`. Existing chats have this baked in; when admin changes `min_whois_length`, the reminder still says "20".

Add a new migration that updates the server_default AND fixes existing rows that still have the old default text.

**Files:**
- Create: `migrations/versions/a1b2c3d4e5f0_fix_whois_reminder_placeholder.py`

- [ ] **Step 1: Create the migration file**

```python
"""fix on_whois_reminder_message: replace hardcoded 20 with MIN_LENGTH placeholder

Revision ID: a1b2c3d4e5f0
Revises: f0g1h2i3j4k5
Create Date: 2026-05-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f0'
down_revision = 'f0g1h2i3j4k5'
branch_labels = None
depends_on = None

_OLD = r'%USER\_MENTION%, напишите сообщение с тегом \#whois (минимум 20 символов), чтобы представиться.'
_NEW = r'%USER\_MENTION%, напишите сообщение с тегом \#whois (минимум %MIN\_LENGTH% символов), чтобы представиться.'


def upgrade():
    op.alter_column(
        'chats', 'on_whois_reminder_message',
        server_default=_NEW,
        existing_type=sa.Text(),
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            "UPDATE chats SET on_whois_reminder_message = :new "
            "WHERE on_whois_reminder_message = :old"
        ).bindparams(old=_OLD, new=_NEW)
    )


def downgrade():
    op.alter_column(
        'chats', 'on_whois_reminder_message',
        server_default=_OLD,
        existing_type=sa.Text(),
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            "UPDATE chats SET on_whois_reminder_message = :old "
            "WHERE on_whois_reminder_message = :new"
        ).bindparams(old=_OLD, new=_NEW)
    )
```

- [ ] **Step 2: Verify single head**

```bash
alembic heads
```

Expected: `a1b2c3d4e5f0 (head)`.

- [ ] **Step 3: Run full suite**

```bash
pytest -v
```

Expected: all pass (>=118).

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/a1b2c3d4e5f0_fix_whois_reminder_placeholder.py
git commit -m "fix: migration to replace hardcoded '20' with %MIN_LENGTH% in reminder default"
```

---

## Self-Review

**Spec coverage:**
- Migration DAG -> Task 1 ✅
- sess.merge data loss -> Task 2 ✅
- ReDoS -> Task 3 ✅
- Markdown injection -> Task 4 ✅
- PTB persistence permissions -> Task 5 ✅
- Unbounded dict -> Task 6 ✅
- start_select_chat auth -> Task 7 ✅
- on_forward design -> Task 8 ✅
- Blocking async calls -> Task 9 ✅
- Migration hardcoded "20" -> Task 10 ✅

**Placeholder scan:** No TBDs, no incomplete steps.

**Type consistency:**
- `_safe_re_search` defined in Task 3, used in filter_message (same task) ✅
- Revision IDs `f0g1h2i3j4k5` and `a1b2c3d4e5f0` used consistently across Tasks 1, 8, 10 ✅
