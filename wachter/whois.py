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
import datetime
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from model import session_scope, User, AlumniProgram, AlumniProgramYear
import constants
import alumni

TEXT_STEPS = ("email", "name")

# минимум осмысленности свободной формы, независимо от настройки чата
_MIN_LETTERS = 10
_MIN_WORDS = 2


def _min_len(state):
    try:
        return int(state.get("min_whois_length") or 20)
    except (TypeError, ValueError):
        return 20


def check_freeform(text, min_len):
    """Простая проверка, что в анкете осмысленный текст, а не «maf27 .......».
    Возвращает текст ошибки, либо None если всё ок. Правила: длина ≥ min_len,
    достаточно букв и минимум два словоподобных токена из букв."""
    t = (text or "").strip()
    if len(t) < min_len:
        return f"Пожалуйста, расскажите о себе подробнее — хотя бы {min_len} символов."
    letters = sum(ch.isalpha() for ch in t)
    words = re.findall(r"[^\W\d_]{2,}", t, re.UNICODE)
    if letters < _MIN_LETTERS or len(words) < _MIN_WORDS:
        return ("Пожалуйста, представьтесь обычным текстом: напишите имя и пару "
                "слов о себе.")
    return None


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
    """Годы выпуска для кнопок, разделённые по категории относительно текущего:
    - выпускник: текущий год включительно и старше (уже выпустился);
    - студент: текущий год включительно и младше — ещё учится (+2 будущих года,
      для BAE +4, т.к. бакалавриат длиннее)."""
    current = datetime.date.today().year
    known = sorted({y for (y,) in session.query(AlumniProgramYear.year)
                    .filter_by(program_code=code).all()})
    if category_choice == "student":
        extra = 4 if code == "BAE" else 2
        return list(range(current, current + extra + 1))
    return [y for y in known if y <= current]


def decade_keyboard(years):
    decades = sorted({y // 10 * 10 for y in years})
    btns = [InlineKeyboardButton(f"{d}-е", callback_data=f"w:dec:{d}") for d in decades]
    return InlineKeyboardMarkup(_rows(btns, 4))


def year_keyboard(years, decade=None):
    """decade=None → показать все года сразу (у студентов их мало)."""
    yy = years if decade is None else [y for y in years if y // 10 * 10 == decade]
    btns = [InlineKeyboardButton(str(y), callback_data=f"w:year:{y}") for y in yy]
    return InlineKeyboardMarkup(_rows(btns, 4))


# --- «Назад» -----------------------------------------------------------------
BACK_BTN = InlineKeyboardButton("⬅️ Назад", callback_data="w:back:_")


def _with_back(markup):
    """Добавляет строку «Назад» под существующей клавиатурой."""
    return InlineKeyboardMarkup(list(markup.inline_keyboard) + [[BACK_BTN]])


def _back_only():
    """Клавиатура из одной кнопки «Назад» — для текстовых шагов (email/имя)."""
    return InlineKeyboardMarkup([[BACK_BTN]])


# --- entry point (called from on_new_chat_member for non-matched users) ----
def _tpl(state, key, default):
    """Chat-configured template from state, else the constant fallback."""
    return (state.get("templates") or {}).get(key) or default


def _name_prompt(state):
    """Анкета зависит от категории: у студента/друга/сотрудника свой текст,
    у выпускника (ручной ввод) — общий name_prompt."""
    choice = state.get("category_choice")
    key, default = {
        "student": ("student_prompt", constants.on_student_prompt_message),
        "friend": ("friend_prompt", constants.on_friend_prompt_message),
        "employee": ("employee_prompt", constants.on_employee_prompt_message),
    }.get(choice, ("name_prompt", constants.whois_ask_name_message))
    return _tpl(state, key, default)


async def start(update, context, chat_id, user_id, username, intro_text, templates=None):
    """Send the intro + category buttons and init state. Returns the sent msg.
    Chat-configured templates are stashed in state (fallback to constants)."""
    templates = templates or {}
    state = {
        "chat_id": chat_id, "username": username, "step": "category",
        "welcome": intro_text,   # чтобы «Назад» смог восстановить экран категорий
        "min_whois_length": (templates or {}).get("min_whois_length") or 20,
        "templates": {
            "alumni_welcome": templates.get("alumni_welcome") or constants.on_alumni_welcome_message,
            "email_prompt": templates.get("email_prompt") or constants.on_email_prompt_message,
            "name_prompt": templates.get("name_prompt") or constants.whois_ask_name_message,
            "student_prompt": templates.get("student_prompt") or constants.on_student_prompt_message,
            "friend_prompt": templates.get("friend_prompt") or constants.on_friend_prompt_message,
            "employee_prompt": templates.get("employee_prompt") or constants.on_employee_prompt_message,
            "introduce": templates.get("introduce") or "🤍 Добро пожаловать в Мишпуху 2.0!",
        },
        "bot_msgs": [],   # id-шники промптов бота — удалим в конце
    }
    _states(context)[user_id] = state
    msg = await update.message.reply_text(
        intro_text, reply_markup=category_keyboard(), parse_mode=ParseMode.MARKDOWN)
    state["bot_msgs"].append(msg.message_id)
    return msg


# --- step rendering (used both forward and for «Назад») --------------------
async def _show_step(query, context, state, step):
    """(Пере)рисовать заданный шаг анкеты in-place. Кнопочные шаги несут «Назад»,
    текстовые (email/имя) — отдельную кнопку «Назад»."""
    state["step"] = step
    if step == "category":
        await query.edit_message_text(
            state.get("welcome") or constants.whois_welcome_message,
            reply_markup=category_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif step == "email":
        await query.edit_message_text(
            _tpl(state, "email_prompt", constants.on_email_prompt_message),
            reply_markup=_back_only())
    elif step == "program":
        with session_scope() as s:
            kb = program_keyboard(s)
        await query.edit_message_text("Выберите программу:", reply_markup=_with_back(kb))
    elif step == "decade":
        await query.edit_message_text(
            "Выберите десятилетие:",
            reply_markup=_with_back(decade_keyboard(state.get("years", []))))
    elif step == "year":
        await query.edit_message_text(
            "Выберите год:",
            reply_markup=_with_back(year_keyboard(state.get("years", []), state.get("decade"))))
    elif step == "role":
        await query.edit_message_text("Кто именно?", reply_markup=_with_back(role_keyboard()))
    elif step == "name":
        await query.edit_message_text(_name_prompt(state), reply_markup=_back_only())


def _prev_step(state):
    """Куда возвращает «Назад» с текущего шага (по линейности флоу)."""
    step = state.get("step")
    choice = state.get("category_choice")
    if step == "email":
        return "category"
    if step == "program":
        return "email" if choice == "alumnus" else "category"
    if step == "decade":
        return "program"
    if step == "year":
        return "decade" if state.get("_had_decade") else "program"
    if step == "role":
        return "category"
    if step == "name":
        return "role" if choice in ("friend", "employee") else "year"
    return None


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

    if kind == "back":
        prev = _prev_step(state)
        if prev:
            await _show_step(query, context, state, prev)
        return

    if kind == "cat":
        if val == "alumnus":
            state["category_choice"] = "alumnus"
            await _show_step(query, context, state, "email")
        elif val == "student":
            state["category_choice"] = "student"
            await _show_step(query, context, state, "program")
        elif val == "friendemp":
            await _show_step(query, context, state, "role")
        return

    if kind == "role":
        state["category_choice"] = val   # friend | employee
        await _show_step(query, context, state, "name")
        return

    if kind == "prog":
        state["program"] = val
        with session_scope() as s:
            years = _program_years(s, val, state.get("category_choice"))
        state["years"] = years
        decades = sorted({y // 10 * 10 for y in years})
        # десятилетия нужны только выпускнику (много исторических лет);
        # у студента лет мало — показываем все года сразу, без шага декады
        use_decades = state.get("category_choice") == "alumnus" and len(decades) > 1
        state["_had_decade"] = use_decades
        if use_decades:
            await _show_step(query, context, state, "decade")
        else:
            state["decade"] = None
            await _show_step(query, context, state, "year")
        return

    if kind == "dec":
        state["decade"] = int(val)
        await _show_step(query, context, state, "year")
        return

    if kind == "year":
        state["year"] = int(val)
        await _show_step(query, context, state, "name")
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
            keyboard = _with_back(program_keyboard(s))
        m = await context.bot.send_message(chat_id, "Не нашли по почте — выберите программу:",
                                           reply_markup=keyboard)
        state.setdefault("bot_msgs", []).append(m.message_id)
        return True

    if state["step"] == "name":
        err = check_freeform(text, _min_len(state))
        if err is not None:
            # непонятный ввод — удаляем и просим переписать; таймер кика идёт
            await _delete_msg(context, chat_id, msg_id)
            m = await context.bot.send_message(chat_id, err)
            state.setdefault("bot_msgs", []).append(m.message_id)
            return True
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
