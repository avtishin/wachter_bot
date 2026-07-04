"""Button-driven whois flow (wachter/whois.py)."""
import datetime
from unittest.mock import MagicMock, AsyncMock

from model import (session_scope, Chat, AlumniPerson, AlumniProgram,
                   AlumniProgramYear, TgIdentity)
import whois
import constants


CHAT_ID = -100


def _seed_programs():
    with session_scope() as s:
        s.add(Chat(id=CHAT_ID, kick_timeout=30))
        s.add(AlumniProgram(code="MAE", title="Master of Arts in Economics [MAE]"))
        s.add(AlumniProgramYear(program_code="MAE", year=2015))
        s.add(AlumniProgramYear(program_code="MAE", year=2019))


def _ctx():
    c = MagicMock()
    c.chat_data = {}
    c.job_queue.get_jobs_by_name = MagicMock(return_value=[])
    c.bot = AsyncMock()
    return c


def _cb(user_id, data):
    u = MagicMock()
    u.effective_user.id = user_id
    u.callback_query.data = data
    u.callback_query.answer = AsyncMock()
    u.callback_query.edit_message_text = AsyncMock()
    return u


def _text(user_id, text):
    u = MagicMock()
    u.effective_user.id = user_id
    u.message.text = text
    u.message.reply_text = AsyncMock()
    return u


async def test_student_full_flow():
    _seed_programs()
    ctx = _ctx()
    uid = 778
    cur = datetime.date.today().year
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "stud", "step": "category"}

    await whois.on_whois_callback(_cb(uid, "w:cat:student"), ctx)
    assert whois._get(ctx, uid)["step"] == "program"

    await whois.on_whois_callback(_cb(uid, "w:prog:MAE"), ctx)
    # у студента только текущий+2 будущих года — все в одном десятилетии,
    # шаг «десятилетие» пропускается, сразу выбор года
    assert whois._get(ctx, uid)["step"] == "year"

    await whois.on_whois_callback(_cb(uid, f"w:year:{cur}"), ctx)
    assert whois._get(ctx, uid)["step"] == "name"

    intro = "Иванов Иван, живу в Москве, увлекаюсь музыкой"
    await whois.try_whois_text(_text(uid, intro), ctx)

    with session_scope() as s:
        ident = s.get(TgIdentity, uid)
        assert ident.category == "student"
        assert ident.declared_year == cur and ident.declared_program == "MAE"
        assert ident.declared_name == intro and ident.source == "buttons"
    assert whois._get(ctx, uid) is None   # state cleared


async def test_alumnus_email_match():
    _seed_programs()
    with session_scope() as s:
        s.add(AlumniPerson(uid="10", name="Tishin Aleksandr", first_name="Aleksandr",
                           last_name="Tishin", emails=["a@nes.ru"], classes=["MAE'2019"],
                           programs=["Master of Arts in Economics [MAE]"], grad_year_max=2019))
    ctx = _ctx()
    uid = 779
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "x",
                               "category_choice": "alumnus", "step": "email"}

    consumed = await whois.try_whois_text(_text(uid, "A@nes.ru"), ctx)
    assert consumed is True
    with session_scope() as s:
        ident = s.get(TgIdentity, uid)
        assert ident.category == "alumni" and ident.alumni_uid == "10"
    assert whois._get(ctx, uid) is None


async def test_alumnus_email_miss_falls_to_program():
    _seed_programs()
    ctx = _ctx()
    uid = 780
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "x",
                               "category_choice": "alumnus", "step": "email"}
    await whois.try_whois_text(_text(uid, "missing@nowhere.com"), ctx)
    st = whois._get(ctx, uid)
    assert st["step"] == "program" and st["declared_email"] == "missing@nowhere.com"


async def test_completion_deletes_input_and_posts_whois():
    _seed_programs()
    ctx = _ctx()
    uid = 800
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "category_choice": "student", "program": "MAE",
                               "year": 2019, "step": "name"}
    await whois.try_whois_text(_text(uid, "Иванов Иван, работаю в X"), ctx)
    # raw input removed from the chat
    ctx.bot.delete_message.assert_called_once()
    # bot posts a searchable #whois summary with program, no email
    chat_id, summary = ctx.bot.send_message.call_args[0][:2]
    assert chat_id == CHAT_ID
    assert "#whois" in summary and "MAE'2019" in summary and "Иванов Иван" in summary


async def test_completion_deletes_bot_prompts_and_friendly_welcome():
    _seed_programs()
    ctx = _ctx()
    uid = 810
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "category_choice": "student", "program": "MAE",
                               "year": 2019, "step": "name", "bot_msgs": [111, 222]}
    await whois.try_whois_text(_text(uid, "Иван Петров, живу в Москве, работаю в X"), ctx)
    # deleted: two bot prompts (111,222) + the user's name message
    assert ctx.bot.delete_message.call_count == 3
    summary = ctx.bot.send_message.call_args[0][1]
    assert "Добро пожаловать в Мишпуху 2.0" in summary and "#whois MAE'2019" in summary


async def test_email_message_is_deleted():
    _seed_programs()
    ctx = _ctx()
    uid = 801
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "category_choice": "alumnus", "step": "email"}
    await whois.try_whois_text(_text(uid, "someone@nowhere.com"), ctx)
    ctx.bot.delete_message.assert_called_once()   # email never lingers


async def test_email_wildcard_does_not_impersonate():
    _seed_programs()
    with session_scope() as s:
        s.add(AlumniPerson(uid="10", name="Real Alum", emails=["real@nes.ru"],
                           classes=["MAE'2019"], programs=[], grad_year_max=2019))
    ctx = _ctx()
    uid = 790
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "x",
                               "category_choice": "alumnus", "step": "email"}
    # entering a LIKE wildcard must NOT link to the alumnus
    await whois.try_whois_text(_text(uid, "%"), ctx)
    with session_scope() as s:
        assert s.get(TgIdentity, uid) is None       # not recognized as alumni
    assert whois._get(ctx, uid)["step"] == "program"  # fell through to manual


async def test_student_years_are_current_and_future_only():
    _seed_programs()   # MAE: исторические 2015, 2019
    cur = datetime.date.today().year
    with session_scope() as s:
        years = whois._program_years(s, "MAE", "student")
    # студент ещё учится: текущий год + 2 будущих, исторические годы отброшены
    assert years == list(range(cur, cur + 3))
    assert 2015 not in years and 2019 not in years


async def test_student_bae_gets_four_future_years():
    cur = datetime.date.today().year
    with session_scope() as s:
        s.add(Chat(id=CHAT_ID))
        s.add(AlumniProgram(code="BAE", title="Bachelor of Arts in Economics [BAE]"))
        s.add(AlumniProgramYear(program_code="BAE", year=2020))
        years = whois._program_years(s, "BAE", "student")
    # бакалавриат длиннее: текущий год + 4 будущих
    assert years == list(range(cur, cur + 5))


async def test_student_bae_skips_decade_and_shows_all_years():
    cur = datetime.date.today().year
    ctx = _ctx()
    uid = 840
    with session_scope() as s:
        s.add(Chat(id=CHAT_ID))
        s.add(AlumniProgram(code="BAE", title="Bachelor of Arts in Economics [BAE]"))
        s.add(AlumniProgramYear(program_code="BAE", year=2020))
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "category_choice": "student", "step": "program"}
    cb = _cb(uid, "w:prog:BAE")
    await whois.on_whois_callback(cb, ctx)
    assert whois._get(ctx, uid)["step"] == "year"       # без шага десятилетия
    labels = _kb_callbacks(cb)
    years = [c for c in labels if c.startswith("w:year:")]
    assert len(years) == 5                               # cur..cur+4 сразу
    assert f"w:year:{cur}" in years and f"w:year:{cur + 4}" in years


async def test_alumnus_multi_decade_uses_decade_step():
    ctx = _ctx()
    uid = 841
    with session_scope() as s:
        s.add(Chat(id=CHAT_ID))
        s.add(AlumniProgram(code="MAE", title="MAE"))
        s.add(AlumniProgramYear(program_code="MAE", year=2008))
        s.add(AlumniProgramYear(program_code="MAE", year=2019))
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "category_choice": "alumnus", "step": "program"}
    await whois.on_whois_callback(_cb(uid, "w:prog:MAE"), ctx)
    assert whois._get(ctx, uid)["step"] == "decade"     # у выпускника декады остаются


async def test_alumnus_years_are_current_and_older():
    _seed_programs()   # MAE: 2015, 2019
    cur = datetime.date.today().year
    with session_scope() as s:
        s.add(AlumniProgramYear(program_code="MAE", year=cur + 50))  # будущий «шум»
        years = whois._program_years(s, "MAE", "alumnus")
    # выпускник уже выпустился: только текущий год и старше
    assert 2015 in years and 2019 in years
    assert all(y <= cur for y in years)


def _kb_callbacks(cb):
    """callback_data всех кнопок в последнем edit_message_text у этого _cb."""
    markup = cb.callback_query.edit_message_text.call_args[1]["reply_markup"]
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_back_button_present_and_returns_to_category():
    _seed_programs()
    ctx = _ctx()
    uid = 901
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "step": "category", "welcome": "Привет"}
    # категория -> программа: клавиатура программы несёт «Назад»
    prog_cb = _cb(uid, "w:cat:student")
    await whois.on_whois_callback(prog_cb, ctx)
    await whois.on_whois_callback(_cb(uid, "w:prog:MAE"), ctx)
    # программа была показана на шаге cat:student — проверим кнопку «Назад» там
    assert "w:back:_" in _kb_callbacks(prog_cb)
    # «Назад» с текущего шага -> обратно к категории (student: year -> program -> category)
    await whois.on_whois_callback(_cb(uid, "w:back:_"), ctx)     # year -> program
    await whois.on_whois_callback(_cb(uid, "w:back:_"), ctx)     # program -> category
    assert whois._get(ctx, uid)["step"] == "category"


def test_check_freeform_rules():
    assert whois.check_freeform("maf27\n\n..........", 20) is not None   # хрень
    assert whois.check_freeform("Иван", 20) is not None                  # коротко
    assert whois.check_freeform("аааааааааааааааааааааа", 20) is not None  # одно слово
    assert whois.check_freeform("12345678901234567890", 20) is not None  # только цифры
    assert whois.check_freeform("Иван Петров, живу в Москве и работаю", 20) is None  # ок


async def test_freeform_rejects_garbage_and_keeps_state():
    _seed_programs()
    ctx = _ctx()
    uid = 820
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "category_choice": "student", "program": "MAE",
                               "year": 2026, "step": "name", "min_whois_length": 20}
    consumed = await whois.try_whois_text(_text(uid, "maf27\n\n.........."), ctx)
    assert consumed is True
    with session_scope() as s:
        assert s.get(TgIdentity, uid) is None           # хрень не записана
    assert whois._get(ctx, uid)["step"] == "name"        # флоу не завершён, таймер идёт
    ctx.bot.send_message.assert_called_once()            # попросили переписать
    ctx.bot.delete_message.assert_called_once()          # мусор удалён из чата


def test_name_prompt_per_category():
    base = {"templates": {}}
    assert whois._name_prompt({**base, "category_choice": "student"}) == constants.on_student_prompt_message
    assert whois._name_prompt({**base, "category_choice": "friend"}) == constants.on_friend_prompt_message
    assert whois._name_prompt({**base, "category_choice": "employee"}) == constants.on_employee_prompt_message
    assert whois._name_prompt({**base, "category_choice": "alumnus"}) == constants.whois_ask_name_message


async def test_friend_flow_shows_friend_prompt():
    _seed_programs()
    ctx = _ctx()
    uid = 831
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "step": "category", "welcome": "hi"}
    await whois.on_whois_callback(_cb(uid, "w:cat:friendemp"), ctx)   # -> role
    cb = _cb(uid, "w:role:friend")
    await whois.on_whois_callback(cb, ctx)                            # -> анкета друга
    assert cb.callback_query.edit_message_text.call_args[0][0] == constants.on_friend_prompt_message


async def test_back_from_year_returns_to_program_when_single_decade():
    _seed_programs()
    ctx = _ctx()
    uid = 902
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "step": "category", "welcome": "Привет"}
    await whois.on_whois_callback(_cb(uid, "w:cat:student"), ctx)
    await whois.on_whois_callback(_cb(uid, "w:prog:MAE"), ctx)   # single decade -> year
    assert whois._get(ctx, uid)["step"] == "year"
    await whois.on_whois_callback(_cb(uid, "w:back:_"), ctx)     # year -> program
    assert whois._get(ctx, uid)["step"] == "program"


async def test_back_from_name_returns_to_role_for_friend():
    _seed_programs()
    ctx = _ctx()
    uid = 903
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "s",
                               "step": "category", "welcome": "Привет"}
    await whois.on_whois_callback(_cb(uid, "w:cat:friendemp"), ctx)  # -> role
    assert whois._get(ctx, uid)["step"] == "role"
    await whois.on_whois_callback(_cb(uid, "w:role:friend"), ctx)    # -> name
    assert whois._get(ctx, uid)["step"] == "name"
    await whois.on_whois_callback(_cb(uid, "w:back:_"), ctx)         # name -> role
    assert whois._get(ctx, uid)["step"] == "role"
