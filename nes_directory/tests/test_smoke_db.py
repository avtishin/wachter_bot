from sqlalchemy import text


def test_postgres_is_reachable(pg_session):
    assert pg_session.execute(text("SELECT 1")).scalar() == 1
