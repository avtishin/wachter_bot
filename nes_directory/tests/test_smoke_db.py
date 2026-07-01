from sqlalchemy import text

import alumni_models as m


def test_postgres_is_reachable(pg_session):
    assert pg_session.execute(text("SELECT 1")).scalar() == 1


def test_person_roundtrip(pg_session):
    pg_session.add(m.AlumniPerson(uid="1", name="Ivanov Ivan", telegram_username="ivan"))
    pg_session.commit()
    got = pg_session.query(m.AlumniPerson).filter_by(uid="1").one()
    assert got.telegram_username == "ivan"
