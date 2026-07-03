"""Bot-side alumni recognition data layer (wachter/alumni.py) + join flow."""
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

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


def test_find_by_email_exact_and_wildcard_safe():
    with session_scope() as s:
        s.add(AlumniPerson(uid="10", name="X Y", emails=["Real@nes.ru"],
                           classes=["MAE'2019"], programs=[], grad_year_max=2019))
    with session_scope() as s:
        # exact match works, case-insensitive
        assert al.find_by_email(s, "real@nes.ru").uid == "10"
        assert al.find_by_email(s, "REAL@nes.ru").uid == "10"
        # LIKE wildcards / non-emails must NOT match anything
        assert al.find_by_email(s, "%") is None
        assert al.find_by_email(s, "_") is None
        assert al.find_by_email(s, '%"%"%') is None
        assert al.find_by_email(s, "notthere@x.com") is None


def test_alumni_whois_message_includes_bio_and_tag():
    alum = SimpleNamespace(
        name="Tishin A", first_name="A", last_name="Tishin",
        classes=["MAE'2019"], programs=[],
        full={"work": [{"position": "Lead", "company": "P2P"}], "residence": "Москва"})
    msg = al.alumni_whois_message("%NAME%, %CLASS%", alum)
    assert "Tishin A, MAE'2019" in msg
    assert "Lead · P2P" in msg and "📍 Москва" in msg
    assert "#whois MAE'2019" in msg


def test_identity_greeting_declared_and_alumnus():
    declared = SimpleNamespace(declared_program="MAE", declared_year=2019,
                               intro="Алекс, работаю в X", declared_name="Алекс")
    m = al.identity_greeting(declared, None, "%NAME%, %CLASS%")
    assert "Алекс, работаю в X" in m and "#whois MAE'2019" in m
    alum = SimpleNamespace(name="Tishin A", first_name="A", last_name="T",
                           classes=["MAE'2019"], programs=[], full={})
    m2 = al.identity_greeting(SimpleNamespace(), alum, "%NAME%, %CLASS%")
    assert "Tishin A, MAE'2019" in m2 and "#whois MAE'2019" in m2


async def test_rejoin_shows_recap_not_snova(mock_context):
    chat_id, uid = -100, 900
    with session_scope() as s:
        s.add(Chat(id=chat_id, on_known_new_chat_member_message="Снова"))
        s.add(AlumniPerson(uid="10", name="Tishin A", first_name="A", last_name="T",
                           classes=["MAE'2019"], programs=[], grad_year_max=2019))
        s.add(User(chat_id=chat_id, user_id=uid, whois="alumni:10"))
        s.add(TgIdentity(user_id=uid, category="alumni", alumni_uid="10"))
    update = make_update(chat_id=chat_id)
    member = MagicMock()
    member.id, member.is_bot, member.username = uid, False, "vbt"
    update.message.new_chat_members = [member]
    update.effective_chat.id = chat_id
    await actions.on_new_chat_member(update, mock_context)
    sent = update.message.reply_text.call_args[0][0]
    assert "Tishin A" in sent and "#whois MAE'2019" in sent   # recap, not "Снова"


def test_apply_timeout():
    assert actions.apply_timeout("есть %TIMEOUT%!", 30) == "есть 30 мин.!"
    out = actions.apply_timeout("Привет (у вас есть %TIMEOUT%):", 0)
    assert "%TIMEOUT%" not in out and "не установлен" not in out and "Привет" in out


async def test_bot_missing_rights():
    def member(**kw):
        return SimpleNamespace(**kw)
    bot = MagicMock()
    bot.id = 1
    bot.get_chat_member = AsyncMock(return_value=member(
        status="administrator", can_restrict_members=True, can_delete_messages=True))
    assert await actions.bot_missing_rights(bot, -100) == []
    bot.get_chat_member = AsyncMock(return_value=member(status="member"))
    assert await actions.bot_missing_rights(bot, -100) == ["admin"]
    bot.get_chat_member = AsyncMock(return_value=member(
        status="administrator", can_restrict_members=True, can_delete_messages=False))
    assert await actions.bot_missing_rights(bot, -100) == ["delete"]


async def test_join_blocked_without_rights(mock_context, monkeypatch):
    monkeypatch.setattr(actions, "ensure_rights", AsyncMock(return_value=False))
    update = make_update(chat_id=-100)
    member = MagicMock()
    member.id, member.is_bot, member.username = 500, False, "x"
    update.message.new_chat_members = [member]
    update.effective_chat.id = -100
    await actions.on_new_chat_member(update, mock_context)
    update.message.reply_text.assert_not_called()   # no processing without rights


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
    sent = update.message.reply_text.call_args[0][0]
    assert sent.startswith("Привет, Aleksandr!")
    assert "#whois MAE'2019" in sent   # searchable tag appended


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
