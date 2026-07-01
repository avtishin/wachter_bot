"""Button-driven whois flow (wachter/whois.py)."""
from unittest.mock import MagicMock, AsyncMock

from model import (session_scope, Chat, AlumniPerson, AlumniProgram,
                   AlumniProgramYear, TgIdentity)
import whois


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
    whois._states(ctx)[uid] = {"chat_id": CHAT_ID, "username": "stud", "step": "category"}

    await whois.on_whois_callback(_cb(uid, "w:cat:student"), ctx)
    assert whois._get(ctx, uid)["step"] == "program"

    await whois.on_whois_callback(_cb(uid, "w:prog:MAE"), ctx)
    # student future years (2020,2021) add a 2020s decade -> pick decade first
    assert whois._get(ctx, uid)["step"] == "decade"

    await whois.on_whois_callback(_cb(uid, "w:dec:2010"), ctx)
    assert whois._get(ctx, uid)["step"] == "year"

    await whois.on_whois_callback(_cb(uid, "w:year:2019"), ctx)
    assert whois._get(ctx, uid)["step"] == "name"

    await whois.try_whois_text(_text(uid, "Иванов Иван"), ctx)

    with session_scope() as s:
        ident = s.get(TgIdentity, uid)
        assert ident.category == "student"
        assert ident.declared_year == 2019 and ident.declared_program == "MAE"
        assert ident.declared_name == "Иванов Иван" and ident.source == "buttons"
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


async def test_student_future_years_have_no_skip():
    _seed_programs()
    with session_scope() as s:
        years = whois._program_years(s, "MAE", "student")
    # base max is 2019, non-BAE adds +2 future years, none dropped
    assert 2021 in years and 2019 in years and 2015 in years
