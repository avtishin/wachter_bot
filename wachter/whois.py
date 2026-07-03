"""Button-driven whois for newcomers not auto-recognized as alumni.

State machine kept in ``context.chat_data['whois'][user_id]`` (chat-scoped so
the newcomer's later callbacks/messages find it regardless of who triggered
the join). Steps:

    category -> [email] -> program -> decade -> year -> name        (alumnus)
    category -> program -> decade -> year -> name                   (student)
    category -> role -> name                                        (friend/employee)

Callbacks use compact ``w:<kind>:<value>`` data (routed by a CallbackQueryHandler
with pattern ``^w:``). Text steps (email, name) are consumed by ``try_whois_text``
hooked at the top of the general text handler.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from model import session_scope, User, AlumniProgram, AlumniProgramYear
import constants
import alumni

TEXT_STEPS = ("email", "name")


# --- state helpers ---------------------------------------------------------
def _states(context):
    return context.chat_data.setdefault("whois", {})


def _get(context, user_id):
    st = context.chat_data.get("whois") if hasattr(context.chat_data, "get") else None
    if isinstance(st, dict):
        s = st.get(user_id)
        return s if isinstance(s, dict) else None
    return None


def _clear(context, user_id):
    st = context.chat_data.get("whois") if hasattr(context.chat_data, "get") else None
    if isinstance(st, dict):
        st.pop(user_id, None)


# --- keyboards -------------------------------------------------------------
def category_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 Я выпускник", callback_data="w:cat:alumnus")],
        [InlineKeyboardButton("📚 Студент РЭШ", callback_data="w:cat:student")],
        [InlineKeyboardButton("🤝 Друг / сотрудник РЭШ", callback_data="w:cat:friendemp")],
    ])


def role_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Друг РЭШ", callback_data="w:role:friend"),
        InlineKeyboardButton("Сотрудник РЭШ", callback_data="w:role:employee"),
    ]])


def _rows(buttons, per_row=2):
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def program_keyboard(session):
    codes = {c for (c,) in session.query(AlumniProgramYear.program_code).distinct()}
    progs = (session.query(AlumniProgram)
             .filter(AlumniProgram.code.in_(codes)).order_by(AlumniProgram.code).all())
    # короткий код (MAE/BAE/…) — полные названия не влезают в кнопку
    btns = [InlineKeyboardButton(p.code, callback_data=f"w:prog:{p.code}")
            for p in progs]
    return InlineKeyboardMarkup(_rows(btns, 3))


def _program_years(session, code, category_choice):
    years = sorted({y for (y,) in session.query(AlumniProgramYear.year)
                    .filter_by(program_code=code).all()})
    if category_choice == "student":
        base = max(years) if years else (alumni.max_grad_year(session) or 0)
        extra = 4 if code == "BAE" else 2
        years = sorted(set(years) | {base + i for i in range(1, extra + 1)})
    return years


def decade_keyboard(years):
    decades = sorted({y // 10 * 10 for y in years})
    btns = [InlineKeyboardButton(f"{d}-е", callback_data=f"w:dec:{d}") for d in decades]
    return InlineKeyboardMarkup(_rows(btns, 4))


def year_keyboard(years, decade):
    yy = [y for y in years if y // 10 * 10 == decade]
    btns = [InlineKeyboardButton(str(y), callback_data=f"w:year:{y}") for y in yy]
    return InlineKeyboardMarkup(_rows(btns, 4))


# --- entry point (called from on_new_chat_member for non-matched users) ----
def _tpl(state, key, default):
    """Chat-configured template from state, else the constant fallback."""
    return (state.get("templates") or {}).get(key) or default


async def start(update, context, chat_id, user_id, username, intro_text, templates=None):
    """Send the intro + category buttons and init state. Returns the sent msg.
    Chat-configured templates are stashed in state (fallback to constants)."""
    templates = templates or {}
    state = {
        "chat_id": chat_id, "username": username, "step": "category",
        "templates": {
            "alumni_welcome": templates.get("alumni_welcome") or constants.on_alumni_welcome_message,
            "email_prompt": templates.get("email_prompt") or constants.on_email_prompt_message,
            "name_prompt": templates.get("name_prompt") or constants.whois_ask_name_message,
            "introduce": templates.get("introduce") or "🤍 Добро пожаловать в Мишпуху 2.0!",
        },
        "bot_msgs": [],   # id-шники промптов бота — удалим в конце
    }
    _states(context)[user_id] = state
    msg = await update.message.reply_text(
        intro_text, reply_markup=category_keyboard(), parse_mode=ParseMode.MARKDOWN)
    state["bot_msgs"].append(msg.message_id)
    return msg


# --- callback handler (pattern ^w:) ----------------------------------------
async def on_whois_callback(update, context):
    query = update.callback_query
    user_id = update.effective_user.id
    state = _get(context, user_id)
    if state is None:
        await query.answer("Это не ваша анкета", show_alert=False)
        return
    await query.answer()
    _, kind, val = query.data.split(":", 2)
    if kind in ("dec", "year") and not val.isdigit():
        return   # ignore crafted non-numeric callback data

    if kind == "cat":
        if val == "alumnus":
            state["category_choice"] = "alumnus"
            state["step"] = "email"
            await query.edit_message_text(
                _tpl(state, "email_prompt", constants.on_email_prompt_message))
        elif val == "student":
            state["category_choice"] = "student"
            state["step"] = "program"
            with session_scope() as s:
                await query.edit_message_text("Выберите программу:",
                                              reply_markup=program_keyboard(s))
        elif val == "friendemp":
            await query.edit_message_text("Кто именно?", reply_markup=role_keyboard())
        return

    if kind == "role":
        state["category_choice"] = val   # friend | employee
        state["step"] = "name"
        await query.edit_message_text(_tpl(state, "name_prompt", constants.whois_ask_name_message))
        return

    if kind == "prog":
        state["program"] = val
        with session_scope() as s:
            years = _program_years(s, val, state.get("category_choice"))
        state["years"] = years
        decades = sorted({y // 10 * 10 for y in years})
        if len(decades) > 1:
            state["step"] = "decade"
            await query.edit_message_text("Выберите десятилетие:",
                                          reply_markup=decade_keyboard(years))
        else:
            state["step"] = "year"
            await query.edit_message_text("Выберите год:",
                                          reply_markup=year_keyboard(years, decades[0] if decades else 0))
        return

    if kind == "dec":
        state["step"] = "year"
        await query.edit_message_text(
            "Выберите год:",
            reply_markup=year_keyboard(state.get("years", []), int(val)))
        return

    if kind == "year":
        state["year"] = int(val)
        state["step"] = "name"
        await query.edit_message_text(_tpl(state, "name_prompt", constants.whois_ask_name_message))
        return


# --- text handler hook (email / name) --------------------------------------
async def try_whois_text(update, context):
    user_id = update.effective_user.id
    state = _get(context, user_id)
    if state is None or state.get("step") not in TEXT_STEPS:
        return False
    text = (update.message.text or "").strip()
    chat_id = state["chat_id"]
    msg_id = update.message.message_id

    if state["step"] == "email":
        # DB work inside the session; network I/O only after it closes.
        welcome = None
        with session_scope() as s:
            alum = alumni.find_by_email(s, text)
            if alum is not None:
                welcome = _link_alumnus(s, state, user_id, alum)
        # the email must not linger in the chat — delete the user's message
        await _delete_msg(context, chat_id, msg_id)
        if welcome is not None:
            await _cancel_kick(context, chat_id, user_id)
            await _cleanup_prompts(context, chat_id, state)
            _clear(context, user_id)
            await context.bot.send_message(chat_id, welcome)
            return True
        # miss -> manual program selection (store only a valid email)
        state["declared_email"] = alumni.valid_email(text)
        state["step"] = "program"
        with session_scope() as s:
            keyboard = program_keyboard(s)
        m = await context.bot.send_message(chat_id, "Не нашли по почте — выберите программу:",
                                           reply_markup=keyboard)
        state.setdefault("bot_msgs", []).append(m.message_id)
        return True

    if state["step"] == "name":
        await _finish_declared(update, context, state, text)
        return True
    return False


# --- completion ------------------------------------------------------------
async def _delete_msg(context, chat_id, message_id):
    """Best-effort delete of a message (needs admin delete rights)."""
    try:
        await context.bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _cleanup_prompts(context, chat_id, state):
    """Delete all of the bot's whois prompt messages once the flow is done."""
    for mid in state.get("bot_msgs", []):
        await _delete_msg(context, chat_id, mid)


def _link_alumnus(session, state, user_id, alum):
    """Bind identity to an alumnus and return the welcome + #whois text."""
    alumni.upsert_identity(session, user_id, username=state.get("username"),
                           category="alumni", alumni_uid=alum.uid,
                           declared_email=state.get("declared_email"), source="buttons")
    session.merge(User(chat_id=state["chat_id"], user_id=user_id,
                       whois=f"alumni:{alum.uid}"))
    template = _tpl(state, "alumni_welcome", constants.on_alumni_welcome_message)
    return alumni.alumni_whois_message(template, alum)


def _declared_tag(state):
    prog, year, choice = state.get("program"), state.get("year"), state.get("category_choice")
    if prog:
        return f"{prog}'{year}" if year else prog
    return {"friend": "друг РЭШ", "employee": "сотрудник РЭШ",
            "student": "студент РЭШ"}.get(choice, "")


async def _finish_declared(update, context, state, text):
    user_id = update.effective_user.id
    chat_id = state["chat_id"]
    choice = state.get("category_choice")
    year = state.get("year")
    with session_scope() as s:
        max_year = alumni.max_grad_year(s)
        category = alumni.classify(choice, year, max_year)
        alumni.upsert_identity(
            s, user_id, username=state.get("username"), category=category,
            declared_name=text, declared_program=state.get("program"),
            declared_year=year, declared_email=state.get("declared_email"),
            intro=text, source="buttons")
        s.merge(User(chat_id=chat_id, user_id=user_id, whois=text))
    # friendly welcome + searchable #whois summary (no email); drop all inputs
    tag = _declared_tag(state)
    intro = _tpl(state, "introduce", "🤍 Добро пожаловать в Мишпуху 2.0!")
    summary = f"{intro}\n{text}"
    if tag:
        summary += f"\n\n#whois {tag}"
    await _delete_msg(context, chat_id, update.message.message_id)  # имя от юзера
    await _cleanup_prompts(context, chat_id, state)                 # промпты бота
    await _cancel_kick(context, chat_id, user_id)
    _clear(context, user_id)
    await context.bot.send_message(chat_id, summary)


async def _cancel_kick(context, chat_id, user_id):
    import actions
    await actions.cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)
