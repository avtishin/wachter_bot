"""Bot-side alumni recognition data layer (wachter/alumni.py) + join flow."""
from unittest.mock import MagicMock

from model import session_scope, AlumniPerson, TgIdentity, User, Chat
import alumni as al
import actions
from helpers import make_update


def _seed_alum(uid="10", username="very_big_t", classes=None):
    with session_scope() as s:
        s.add(AlumniPerson(
            uid=uid, name="Tishin Aleksandr", first_name="Aleksandr",
            last_name="Tishin", telegram_username=username,
            classes=classes or ["MAE'2019"],
            programs=["Master of Arts in Economics [MAE]"], grad_year_max=2019))


def test_find_by_username():
    _seed_alum()
    with session_scope() as s:
        a = al.find_by_username(s, "@Very_Big_T")
        assert a is not None and a.uid == "10"
        assert al.find_by_username(s, "nobody") is None
        assert al.find_by_username(s, None) is None


def test_format_welcome():
    _seed_alum()
    with session_scope() as s:
        a = al.find_by_username(s, "very_big_t")
        msg = al.format_welcome("%NAME%, %CLASS% — добро пожаловать!", a)
        assert msg == "Tishin Aleksandr, MAE'2019 — добро пожаловать!"


def test_upsert_identity_alumni_sets_verified():
    _seed_alum()
    with session_scope() as s:
        a = al.find_by_username(s, "very_big_t")
        al.upsert_identity(s, 95, username="@very_big_t", category="alumni", alumni_uid=a.uid)
    with session_scope() as s:
        i = s.get(TgIdentity, 95)
        assert i.category == "alumni" and i.alumni_uid == "10"
        assert i.username == "very_big_t" and i.verified_at is not None


def test_classify():
    assert al.classify("alumnus", 2026, 2025) == "student"
    assert al.classify("alumnus", 2018, 2025) == "unresolved_alumni"
    assert al.classify("student", None, 2025) == "student"
    assert al.classify("friend", None, 2025) == "friend"


async def test_join_recognizes_alumnus(mock_context):
    chat_id, uid = -100, 777
    with session_scope() as s:
        s.add(Chat(id=chat_id))
        s.add(AlumniPerson(uid="10", name="Tishin Aleksandr", first_name="Aleksandr",
                           last_name="Tishin", telegram_username="very_big_t",
                           classes=["MAE'2019"],
                           programs=["Master of Arts in Economics [MAE]"], grad_year_max=2019))

    update = make_update(chat_id=chat_id)
    member = MagicMock()
    member.id, member.is_bot, member.username = uid, False, "very_big_t"
    update.message.new_chat_members = [member]
    update.effective_chat.id = chat_id

    await actions.on_new_chat_member(update, mock_context)

    update.message.reply_text.assert_called_once()
    sent = update.message.reply_text.call_args[0][0]
    assert "Tishin Aleksandr" in sent and "MAE'2019" in sent
    mock_context.job_queue.run_once.assert_not_called()   # no kick for alumni

    with session_scope() as s:
        ident = s.get(TgIdentity, uid)
        assert ident.category == "alumni" and ident.alumni_uid == "10"
        u = s.query(User).filter(User.chat_id == chat_id, User.user_id == uid).first()
        assert u is not None


async def test_join_uses_custom_alumni_template(mock_context):
    chat_id, uid = -100, 781
    with session_scope() as s:
        s.add(Chat(id=chat_id, on_alumni_welcome_message="Привет, %FIRST_NAME%!"))
        s.add(AlumniPerson(uid="10", name="Tishin Aleksandr", first_name="Aleksandr",
                           last_name="Tishin", telegram_username="vbt",
                           classes=["MAE'2019"], programs=[], grad_year_max=2019))
    update = make_update(chat_id=chat_id)
    member = MagicMock()
    member.id, member.is_bot, member.username = uid, False, "vbt"
    update.message.new_chat_members = [member]
    update.effective_chat.id = chat_id

    await actions.on_new_chat_member(update, mock_context)
    assert update.message.reply_text.call_args[0][0] == "Привет, Aleksandr!"


async def test_join_non_alumnus_uses_whois_flow(mock_context):
    chat_id, uid = -100, 778
    with session_scope() as s:
        s.add(Chat(id=chat_id, kick_timeout=30))

    update = make_update(chat_id=chat_id)
    member = MagicMock()
    member.id, member.is_bot, member.username = uid, False, "stranger"
    update.message.new_chat_members = [member]
    update.effective_chat.id = chat_id

    await actions.on_new_chat_member(update, mock_context)

    # no alumni identity recorded; kick timer scheduled (standard flow)
    with session_scope() as s:
        assert s.get(TgIdentity, uid) is None
    assert mock_context.job_queue.run_once.called
