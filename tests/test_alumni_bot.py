"""Bot-side alumni recognition data layer (wachter/alumni.py)."""
from model import session_scope, AlumniPerson, TgIdentity
import alumni as al


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
