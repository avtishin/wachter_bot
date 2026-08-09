import alumni_models as m
import alumni_link as al


def _alum(session, uid, name, username=None, emails=None, classes=None):
    gmax = max(int(c.split("'")[-1]) for c in classes) if classes else None
    session.add(m.AlumniPerson(uid=uid, name=name, telegram_username=username,
                               emails=emails or [], classes=classes or [],
                               grad_year_max=gmax))
    session.commit()


def test_normalize():
    assert al.normalize_username("@Very_Big_T") == "very_big_t"
    assert al.normalize_username("https://t.me/Foo") == "foo"
    assert al.normalize_username("  ") is None
    assert al.normalize_email("  A@NES.ru ") == "a@nes.ru"
    assert al.normalize_email("") is None


def test_find_by_username(pg_session):
    _alum(pg_session, "1", "Ivanov Ivan", username="ivan", classes=["MAE'2019"])
    assert al.find_by_username(pg_session, "@Ivan").uid == "1"
    assert al.find_by_username(pg_session, "nope") is None


def test_find_by_email(pg_session):
    _alum(pg_session, "2", "Petrov Petr", emails=["p@gmail.com"], classes=["MAE'2015"])
    assert al.find_by_email(pg_session, "P@gmail.com").uid == "2"
    assert al.find_by_email(pg_session, "x@y.z") is None


def test_max_grad_year(pg_session):
    _alum(pg_session, "3", "A B", classes=["MAE'2019"])
    _alum(pg_session, "4", "C D", classes=["BAE'2023"])
    assert al.max_grad_year(pg_session) == 2023


def test_classify():
    assert al.classify("alumnus", 2026, 2025) == "student"       # future year -> student
    assert al.classify("alumnus", 2018, 2025) == "unresolved_alumni"
    assert al.classify("student", 2027, 2025) == "student"
    assert al.classify("friend", None, 2025) == "friend"
    assert al.classify("employee", None, 2025) == "employee"


def test_upsert_identity(pg_session):
    i = al.upsert_identity(pg_session, 42, username="@Foo", category="unknown", source="members_csv")
    assert i.username == "foo" and i.category == "unknown"
    i2 = al.upsert_identity(pg_session, 42, category="alumni", alumni_uid="1")
    assert i2.category == "alumni" and i2.alumni_uid == "1"
    assert i2.username == "foo"          # unchanged fields survive
    assert i2.verified_at is not None    # alumni sets verified_at


def test_reconcile_by_username(pg_session):
    _alum(pg_session, "10", "Tishin A", username="very_big_t", classes=["MAE'2019"])
    al.upsert_identity(pg_session, 95, username="very_big_t", category="unknown", source="members_csv")
    res = al.reconcile(pg_session)
    assert res["linked"] == 1
    i = pg_session.get(m.TgIdentity, 95)
    assert i.category == "alumni" and i.alumni_uid == "10"


def test_reconcile_by_email(pg_session):
    _alum(pg_session, "11", "Petrov P", emails=["p@gmail.com"], classes=["MAE'2015"])
    al.upsert_identity(pg_session, 96, category="unresolved_alumni",
                       declared_email="p@gmail.com", source="buttons")
    res = al.reconcile(pg_session)
    assert res["linked"] == 1
    assert pg_session.get(m.TgIdentity, 96).alumni_uid == "11"


def test_reconcile_skips_resolved_and_friends(pg_session):
    _alum(pg_session, "12", "X Y", username="known", classes=["MAE'2019"])
    al.upsert_identity(pg_session, 97, username="known", category="friend", source="buttons")
    res = al.reconcile(pg_session)
    assert res["linked"] == 0           # friends are not auto-relinked
    assert pg_session.get(m.TgIdentity, 97).category == "friend"
