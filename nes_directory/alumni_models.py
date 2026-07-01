import os
from contextlib import contextmanager

from sqlalchemy import create_engine, Column, Text, Integer, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class AlumniPerson(Base):
    __tablename__ = "alumni_person"
    uid = Column(Text, primary_key=True)
    name = Column(Text)
    first_name = Column(Text)
    last_name = Column(Text)
    sex = Column(Text)
    birthday = Column(Text)
    residence = Column(Text)
    telegram_username = Column(Text, index=True)
    programs = Column(JSONB)
    classes = Column(JSONB)
    grad_year_max = Column(Integer)
    content_hash = Column(Text)
    full = Column(JSONB)
    first_seen = Column(TIMESTAMP(timezone=True))
    last_seen = Column(TIMESTAMP(timezone=True))
    last_changed = Column(TIMESTAMP(timezone=True))
    removed_at = Column(TIMESTAMP(timezone=True))


class AlumniPersonHistory(Base):
    __tablename__ = "alumni_person_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(Text, index=True)
    content_hash = Column(Text)
    captured_at = Column(TIMESTAMP(timezone=True))
    data = Column(JSONB)


class AlumniChangeLog(Base):
    __tablename__ = "alumni_change_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(Text, index=True)
    captured_at = Column(TIMESTAMP(timezone=True))
    change_type = Column(Text)
    field = Column(Text)
    old_value = Column(Text)
    new_value = Column(Text)


class AlumniCrawl(Base):
    __tablename__ = "alumni_crawl"
    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(TIMESTAMP(timezone=True))
    finished_at = Column(TIMESTAMP(timezone=True))
    kind = Column(Text)
    n_seen = Column(Integer)
    n_new = Column(Integer)
    n_changed = Column(Integer)
    n_removed = Column(Integer)


class AlumniRawCard(Base):
    __tablename__ = "alumni_raw_card"
    uid = Column(Text, primary_key=True)
    html = Column(Text)
    fetched_at = Column(TIMESTAMP(timezone=True))


class AlumniProgram(Base):
    __tablename__ = "alumni_program"
    code = Column(Text, primary_key=True)
    title = Column(Text)


class AlumniProgramYear(Base):
    __tablename__ = "alumni_program_year"
    program_code = Column(Text, primary_key=True)
    year = Column(Integer, primary_key=True)


def get_engine(url=None):
    url = url or os.environ.get("DATABASE_URL", "postgresql://localhost:5432/wachter")
    url = url.replace("postgres://", "postgresql://", 1)
    return create_engine(url)


_default_engine = None


def _resolve_engine(engine=None):
    global _default_engine
    if engine is not None:
        return engine
    if _default_engine is None:
        _default_engine = get_engine()
    return _default_engine


def init_db(engine=None):
    engine = _resolve_engine(engine)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(engine=None):
    Session = sessionmaker(bind=_resolve_engine(engine))
    sess = Session()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
