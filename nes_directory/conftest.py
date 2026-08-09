import pytest
from testcontainers.postgres import PostgresContainer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url()  # postgresql+psycopg2://...
        yield url


@pytest.fixture()
def pg_session(pg_url):
    import alumni_models as m
    engine = create_engine(pg_url)
    m.Base.metadata.drop_all(engine)
    m.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()
