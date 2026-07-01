# Alumni Store → Postgres Migration (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the alumni directory store from SQLite (`out/nes.db`) to Postgres via SQLAlchemy, adding derived fields and keyboard-reference tables, so the bot and dashboard can share one database.

**Architecture:** New SQLAlchemy models (`alumni_models.py`) own the `alumni_*` tables. Pure derivation helpers (`alumni_derive.py`) extract telegram usernames, split names, and build program/year references. `nes_db.py` keeps its pure `canonical/hash/diff` helpers but its `connect/ingest/stats/changes` are rewritten against Postgres. The dashboard (`app.py`) reads Postgres. Tests run against an ephemeral Postgres via testcontainers.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, psycopg2-binary, Postgres 16, pytest, testcontainers, Flask.

## Global Constraints

- Single Postgres via `DATABASE_URL` env; SQLAlchemy 2.x; `postgres://` normalized to `postgresql://` (Railway).
- Alumni tables are prefixed `alumni_` and owned by the scraper (`nes_directory/`).
- All tests run against ephemeral Postgres (no SQLite anywhere in the new code).
- Politeness/scraping behavior unchanged; only the storage layer changes in this phase.
- `telegram_username` is normalized: lower-case, no `@`, no query string.
- Name split: directory names are `Last First Patronymic` → `last_name = tokens[0]`, `first_name = tokens[1]`.

---

## File Structure

- Create `nes_directory/alumni_models.py` — SQLAlchemy `Base`, ORM models for all `alumni_*` tables, engine + `session_scope()`.
- Create `nes_directory/alumni_derive.py` — pure helpers (name split, telegram username, year/program parsing, program-year reference builder).
- Create `nes_directory/conftest.py` — pytest fixtures: session-scoped Postgres container, function-scoped clean DB session.
- Create `nes_directory/tests/test_derive.py`, `tests/test_ingest.py`, `tests/test_dashboard.py`.
- Modify `nes_directory/nes_db.py` — rewrite `connect/ingest/stats/changes` on SQLAlchemy; keep `canonical/content_hash/diff/_flatten/_as_text/_canon/CONTENT_FIELDS` unchanged.
- Modify `nes_directory/app.py` — replace `sqlite3` access with SQLAlchemy sessions/queries.
- Modify `nes_directory/requirements.txt` — add SQLAlchemy, psycopg2-binary, pytest, testcontainers.
- Modify `nes_directory/docker-compose.yml` — add a `db` Postgres service; pass `DATABASE_URL` to `web`.

---

## Task 1: Dependencies + ephemeral Postgres test fixture

**Files:**
- Modify: `nes_directory/requirements.txt`
- Create: `nes_directory/conftest.py`
- Create: `nes_directory/tests/test_smoke_db.py`

**Interfaces:**
- Produces: pytest fixture `pg_session` → a SQLAlchemy `Session` bound to a clean ephemeral Postgres with all `alumni_*` tables created; fixture `pg_url` → the container's `DATABASE_URL` string.

- [ ] **Step 1: Add dependencies**

Edit `nes_directory/requirements.txt` to:

```
requests>=2.28,<3
beautifulsoup4>=4.11,<5
Flask>=3.0,<4
gunicorn>=21,<24
croniter>=2.0,<4
SQLAlchemy>=2.0,<3
psycopg2-binary>=2.9,<3
pytest>=8.0,<9
testcontainers>=4.0,<5
```

- [ ] **Step 2: Install into the venv**

Run: `cd nes_directory && ./.venv/bin/pip install -r requirements.txt`
Expected: installs SQLAlchemy, psycopg2-binary, pytest, testcontainers without error.

- [ ] **Step 3: Write the fixtures (will fail to import models until Task 2)**

Create `nes_directory/conftest.py`:

```python
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
```

- [ ] **Step 4: Write a smoke test**

Create `nes_directory/tests/test_smoke_db.py`:

```python
from sqlalchemy import text


def test_postgres_is_reachable(pg_session):
    assert pg_session.execute(text("SELECT 1")).scalar() == 1
```

- [ ] **Step 5: Run — expect failure (no alumni_models yet)**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_smoke_db.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'alumni_models'`.

- [ ] **Step 6: Commit**

```bash
git add nes_directory/requirements.txt nes_directory/conftest.py nes_directory/tests/test_smoke_db.py
git commit -m "test: ephemeral Postgres fixture + deps for alumni migration"
```

---

## Task 2: SQLAlchemy models for alumni_* tables

**Files:**
- Create: `nes_directory/alumni_models.py`

**Interfaces:**
- Produces:
  - `Base` (declarative base).
  - Models `AlumniPerson, AlumniPersonHistory, AlumniChangeLog, AlumniCrawl, AlumniRawCard, AlumniProgram, AlumniProgramYear`.
  - `get_engine(url=None) -> Engine` (reads `DATABASE_URL`, normalizes `postgres://`).
  - `session_scope() -> ContextManager[Session]` (commit/rollback/close).
  - `init_db(engine=None)` calling `Base.metadata.create_all`.

- [ ] **Step 1: Write the models module**

Create `nes_directory/alumni_models.py`:

```python
import os
from contextlib import contextmanager

from sqlalchemy import (create_engine, Column, Text, Integer, BigInteger,
                        TIMESTAMP)
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


_Session = None


def init_db(engine=None):
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(engine=None):
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=engine or get_engine())
    sess = _Session()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
```

- [ ] **Step 2: Run the Task 1 smoke test — now passes**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_smoke_db.py -v`
Expected: PASS (fixture imports `alumni_models`, tables create, `SELECT 1` works).

- [ ] **Step 3: Add a model round-trip test**

Append to `nes_directory/tests/test_smoke_db.py`:

```python
def test_person_roundtrip(pg_session):
    import alumni_models as m
    pg_session.add(m.AlumniPerson(uid="1", name="Ivanov Ivan", telegram_username="ivan"))
    pg_session.commit()
    got = pg_session.query(m.AlumniPerson).filter_by(uid="1").one()
    assert got.telegram_username == "ivan"
```

- [ ] **Step 4: Run**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_smoke_db.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add nes_directory/alumni_models.py nes_directory/tests/test_smoke_db.py
git commit -m "feat: SQLAlchemy models for alumni_* tables"
```

---

## Task 3: Pure derivation helpers

**Files:**
- Create: `nes_directory/alumni_derive.py`
- Create: `nes_directory/tests/test_derive.py`

**Interfaces:**
- Produces:
  - `split_name(name: str) -> tuple[str|None, str|None]` → `(first_name, last_name)`.
  - `telegram_username(links: list[dict]) -> str|None` (normalized).
  - `emails(contact: dict) -> list[str]` (lower-cased, de-duped, order-preserving).
  - `year_of_class(cls: str) -> int|None` (trailing 4-digit year).
  - `program_code_of_class(cls: str) -> str|None` (prefix before `'`).
  - `grad_year_max(classes: list[str]) -> int|None`.
  - `program_title_map(programs: list[str]) -> dict[str,str]` (code→title from `[CODE]`).
  - `build_program_years(people: Iterable[dict]) -> tuple[dict[str,str], set[tuple[str,int]]]` → `(code→title, {(code, year)})`.

- [ ] **Step 1: Write failing tests**

Create `nes_directory/tests/test_derive.py`:

```python
import alumni_derive as d


def test_split_name():
    assert d.split_name("Vylegzhanin Aleksandr Sergeevich") == ("Aleksandr", "Vylegzhanin")
    assert d.split_name("Madonna") == (None, "Madonna")
    assert d.split_name("") == (None, None)


def test_telegram_username_normalizes():
    links = [{"title": "LinkedIn", "url": "https://www.linkedin.com/in/x"},
             {"title": "Telegram", "url": "https://t.me/Very_Big_T"}]
    assert d.telegram_username(links) == "very_big_t"
    assert d.telegram_username([]) is None
    assert d.telegram_username([{"url": "https://t.me/user?start=1"}]) == "user"


def test_year_and_program_of_class():
    assert d.year_of_class("MAE'2019") == 2019
    assert d.program_code_of_class("MAE'2019") == "MAE"
    assert d.year_of_class("no year") is None


def test_emails():
    contact = {"emails": ["A@nes.ru", "b@x.com", "a@nes.ru"]}
    assert d.emails(contact) == ["a@nes.ru", "b@x.com"]
    assert d.emails({}) == []
    assert d.emails({"emails": []}) == []


def test_grad_year_max():
    assert d.grad_year_max(["BAE'2017", "MAE'2019"]) == 2019
    assert d.grad_year_max([]) is None


def test_program_title_map():
    m = d.program_title_map(["Master of Arts in Economics [MAE]", "PhD [PhD]"])
    assert m == {"MAE": "Master of Arts in Economics [MAE]", "PhD": "PhD [PhD]"}


def test_build_program_years():
    people = [
        {"listed_classes": ["MAE'2019"], "listed_programs": ["Master of Arts in Economics [MAE]"]},
        {"listed_classes": ["MAE'2012", "BAE'2015"],
         "listed_programs": ["Master of Arts in Economics [MAE]", "Bachelor of Arts in Economics [BAE]"]},
    ]
    titles, pairs = d.build_program_years(people)
    assert titles["BAE"] == "Bachelor of Arts in Economics [BAE]"
    assert ("MAE", 2019) in pairs and ("MAE", 2012) in pairs and ("BAE", 2015) in pairs
```

- [ ] **Step 2: Run — expect failure**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_derive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'alumni_derive'`.

- [ ] **Step 3: Implement the helpers**

Create `nes_directory/alumni_derive.py`:

```python
import re

_TG_RE = re.compile(r"(?:t|telegram)\.me/([A-Za-z0-9_]+)")
_YEAR_RE = re.compile(r"(\d{4})")
_CODE_RE = re.compile(r"\[([^\]]+)\]\s*$")


def split_name(name):
    parts = (name or "").split()
    if not parts:
        return (None, None)
    if len(parts) == 1:
        return (None, parts[0])
    return (parts[1], parts[0])


def telegram_username(links):
    for l in links or []:
        m = _TG_RE.search(l.get("url", ""))
        if m:
            return m.group(1).lower()
    return None


def emails(contact):
    out = []
    for e in (contact or {}).get("emails") or []:
        e = (e or "").strip().lower()
        if e and e not in out:
            out.append(e)
    return out


def year_of_class(cls):
    m = _YEAR_RE.search(cls or "")
    return int(m.group(1)) if m else None


def program_code_of_class(cls):
    if not cls or "'" not in cls:
        return None
    return cls.split("'", 1)[0].strip() or None


def grad_year_max(classes):
    years = [y for y in (year_of_class(c) for c in (classes or [])) if y]
    return max(years) if years else None


def program_title_map(programs):
    out = {}
    for title in programs or []:
        m = _CODE_RE.search(title)
        if m:
            out[m.group(1)] = title
    return out


def build_program_years(people):
    titles = {}
    pairs = set()
    for p in people:
        titles.update(program_title_map(p.get("listed_programs")))
        for c in p.get("listed_classes") or []:
            code = program_code_of_class(c)
            year = year_of_class(c)
            if code and year:
                pairs.add((code, year))
    return titles, pairs
```

- [ ] **Step 4: Run — expect pass**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_derive.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add nes_directory/alumni_derive.py nes_directory/tests/test_derive.py
git commit -m "feat: pure derivation helpers (name/telegram/program-year)"
```

---

## Task 4: Rewrite `ingest()` against Postgres

**Files:**
- Modify: `nes_directory/nes_db.py`
- Create: `nes_directory/tests/test_ingest.py`

**Interfaces:**
- Consumes: `alumni_models` (Task 2), `alumni_derive` (Task 3), existing pure `canonical/content_hash/diff` in `nes_db.py`.
- Produces:
  - `ingest(kind="full", records=None, cards=None, engine=None) -> dict` — upserts people, writes history/changelog/crawl, rebuilds program tables, stores raw cards; returns `{"seen","new","changed","removed"}`.
  - `stats(engine=None) -> dict`, `changes(n=30, engine=None) -> list[dict]`.
  - `load_records() -> list[dict]` (reads `out/alumni.json`), `load_cards() -> dict[str,str]` (reads `raw_html/cards/*.html`).

- [ ] **Step 1: Write failing tests**

Create `nes_directory/tests/test_ingest.py`:

```python
import copy
import nes_db
import alumni_models as m

REC = {
    "uid": "10", "name": "Tishin Aleksandr Vladimirovich", "sex": "Male",
    "birthday": "24 January", "residence": "Россия, г Москва",
    "listed_programs": ["Master of Arts in Economics [MAE]"],
    "listed_classes": ["MAE'2019"],
    "contact": {"links": [{"title": "Telegram", "url": "https://t.me/Very_Big_T"}],
                "emails": ["A.Tishin@nes.ru"]},
    "work": [{"company": "X", "position": "Lead"}],
}


def _engine(pg_session):
    return pg_session.get_bind()


def test_ingest_new_person(pg_session):
    eng = _engine(pg_session)
    res = nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={"10": "<html>x</html>"}, engine=eng)
    assert res["new"] == 1 and res["changed"] == 0
    p = pg_session.query(m.AlumniPerson).filter_by(uid="10").one()
    assert p.telegram_username == "very_big_t"
    assert p.emails == ["a.tishin@nes.ru"]
    assert p.first_name == "Aleksandr" and p.last_name == "Tishin"
    assert p.grad_year_max == 2019
    assert pg_session.query(m.AlumniProgramYear).filter_by(program_code="MAE", year=2019).count() == 1
    assert pg_session.query(m.AlumniRawCard).filter_by(uid="10").one().html == "<html>x</html>"
    assert pg_session.query(m.AlumniChangeLog).filter_by(uid="10", change_type="created").count() == 1


def test_ingest_detects_change(pg_session):
    eng = _engine(pg_session)
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=eng)
    rec2 = copy.deepcopy(REC)
    rec2["work"] = [{"company": "Y", "position": "CEO"}]
    res = nes_db.ingest("full", records=[rec2], cards={}, engine=eng)
    assert res["changed"] == 1
    assert pg_session.query(m.AlumniPersonHistory).filter_by(uid="10").count() == 2


def test_ingest_full_marks_removed(pg_session):
    eng = _engine(pg_session)
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=eng)
    res = nes_db.ingest("full", records=[], cards={}, engine=eng)
    assert res["removed"] == 1
    assert pg_session.query(m.AlumniPerson).filter_by(uid="10").one().removed_at is not None


def test_ingest_new_kind_does_not_remove(pg_session):
    eng = _engine(pg_session)
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=eng)
    res = nes_db.ingest("new", records=[], cards={}, engine=eng)
    assert res["removed"] == 0
```

- [ ] **Step 2: Run — expect failure**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: FAIL — `ingest()` signature/behavior mismatch (current version reads SQLite/`out/alumni.json`).

- [ ] **Step 3: Rewrite the persistence part of `nes_db.py`**

In `nes_directory/nes_db.py`: keep the imports of `json/hashlib/pathlib`, and keep `CONTENT_FIELDS`, `_canon`, `canonical`, `content_hash`, `_flatten`, `diff`, `_as_text` exactly as they are. Remove `import sqlite3`, the `SCHEMA` string, and the old `connect()`. Replace `now()`, `ingest()`, `stats()`, `changes()`, and add loaders. New code:

```python
from datetime import datetime, timezone

import alumni_models as m
import alumni_derive as d
from alumni_models import get_engine, init_db, AlumniPerson, AlumniPersonHistory, \
    AlumniChangeLog, AlumniCrawl, AlumniRawCard, AlumniProgram, AlumniProgramYear
from sqlalchemy.orm import sessionmaker


def now():
    return datetime.now(timezone.utc)


def load_records():
    p = OUT / "alumni.json"
    if not p.exists():
        raise SystemExit("Нет out/alumni.json — сначала запусти фазу parse.")
    return json.loads(p.read_text(encoding="utf-8"))


def load_cards():
    cards = {}
    d_ = OUT.parent / "raw_html" / "cards"
    for f in d_.glob("*.html") if d_.exists() else []:
        cards[f.stem] = f.read_text(encoding="utf-8")
    return cards


def _session_factory(engine):
    return sessionmaker(bind=engine or get_engine())


def ingest(kind="full", records=None, cards=None, engine=None):
    engine = engine or get_engine()
    init_db(engine)
    if records is None:
        records = load_records()
    if cards is None:
        cards = load_cards()
    Session = _session_factory(engine)
    sess = Session()
    started = now()
    seen = set()
    n_new = n_changed = 0
    try:
        for rec in records:
            uid = str(rec["uid"])
            seen.add(uid)
            canon = canonical(rec)
            h = content_hash(canon)
            full = {k: v for k, v in rec.items() if not k.startswith("_")}
            first, last = d.split_name(rec.get("name") or "")
            tg = d.telegram_username((rec.get("contact") or {}).get("links"))
            emls = d.emails(rec.get("contact") or {})
            classes = rec.get("listed_classes") or []
            programs = rec.get("listed_programs") or []
            gmax = d.grad_year_max(classes)
            name = rec.get("name") or rec.get("listed_name") or ""
            ts = now()
            row = sess.get(AlumniPerson, uid)
            if row is None:
                sess.add(AlumniPerson(
                    uid=uid, name=name, first_name=first, last_name=last,
                    sex=rec.get("sex"), birthday=rec.get("birthday"),
                    residence=rec.get("residence"), telegram_username=tg, emails=emls,
                    programs=programs, classes=classes, grad_year_max=gmax,
                    content_hash=h, full=full, first_seen=ts, last_seen=ts,
                    last_changed=ts, removed_at=None))
                sess.add(AlumniPersonHistory(uid=uid, content_hash=h, captured_at=ts, data=canon))
                sess.add(AlumniChangeLog(uid=uid, captured_at=ts, change_type="created",
                                         field="", old_value=None, new_value=name))
                n_new += 1
            elif row.content_hash != h:
                old_canon = sess.query(AlumniPersonHistory.data).filter_by(
                    uid=uid).order_by(AlumniPersonHistory.id.desc()).first()
                old = old_canon[0] if old_canon else {}
                for ctype, field, ov, nv in diff(old, canon):
                    sess.add(AlumniChangeLog(uid=uid, captured_at=ts, change_type=ctype,
                                             field=field, old_value=ov, new_value=nv))
                sess.add(AlumniPersonHistory(uid=uid, content_hash=h, captured_at=ts, data=canon))
                row.name, row.first_name, row.last_name = name, first, last
                row.sex, row.birthday, row.residence = rec.get("sex"), rec.get("birthday"), rec.get("residence")
                row.telegram_username, row.programs, row.classes = tg, programs, classes
                row.emails = emls
                row.grad_year_max, row.content_hash, row.full = gmax, h, full
                row.last_seen = row.last_changed = ts
                row.removed_at = None
                n_changed += 1
            else:
                row.last_seen = ts
                row.removed_at = None
            html = cards.get(uid)
            if html is not None:
                card = sess.get(AlumniRawCard, uid)
                if card is None:
                    sess.add(AlumniRawCard(uid=uid, html=html, fetched_at=ts))
                else:
                    card.html, card.fetched_at = html, ts

        n_removed = 0
        if kind == "full":
            ts = now()
            for row in sess.query(AlumniPerson).filter(AlumniPerson.removed_at.is_(None)).all():
                if row.uid not in seen:
                    row.removed_at = ts
                    sess.add(AlumniChangeLog(uid=row.uid, captured_at=ts, change_type="removed",
                                             field="", old_value="present", new_value=None))
                    n_removed += 1

        titles, pairs = d.build_program_years(records)
        sess.query(AlumniProgramYear).delete()
        sess.query(AlumniProgram).delete()
        for code, title in titles.items():
            sess.add(AlumniProgram(code=code, title=title))
        for code, year in pairs:
            sess.add(AlumniProgramYear(program_code=code, year=year))

        sess.add(AlumniCrawl(started_at=started, finished_at=now(), kind=kind,
                             n_seen=len(seen), n_new=n_new, n_changed=n_changed, n_removed=n_removed))
        sess.commit()
        print(f"ingest[{kind}]: seen={len(seen)} new={n_new} changed={n_changed} removed={n_removed}")
        return {"seen": len(seen), "new": n_new, "changed": n_changed, "removed": n_removed}
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
```

Replace the `if __name__ == "__main__"` block's `stats/changes` handling with the versions below.

- [ ] **Step 4: Update the CLI `__main__` block**

At the bottom of `nes_db.py`, ensure the CLI still works against Postgres (no `load_index()` dependency). Replace the `if __name__ == "__main__"` block with:

```python
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ingest"
    if cmd == "ingest":
        kind = sys.argv[sys.argv.index("--kind") + 1] if "--kind" in sys.argv else "full"
        ingest(kind)
    elif cmd == "stats":
        stats()
    elif cmd == "changes":
        changes(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        raise SystemExit(f"unknown command {cmd!r}")
```

Add `import sys` at the top of `nes_db.py` if not already present.

- [ ] **Step 5: Rewrite `stats()` and `changes()`**

Replace the bodies of `stats` and `changes` in `nes_db.py`:

```python
def stats(engine=None):
    Session = _session_factory(engine or get_engine())
    sess = Session()
    try:
        total = sess.query(AlumniPerson).count()
        active = sess.query(AlumniPerson).filter(AlumniPerson.removed_at.is_(None)).count()
        hist = sess.query(AlumniPersonHistory).count()
        chg = sess.query(AlumniChangeLog).count()
        out = {"persons": total, "active": active, "history": hist, "changes": chg}
        print(out)
        return out
    finally:
        sess.close()


def changes(n=30, engine=None):
    Session = _session_factory(engine or get_engine())
    sess = Session()
    try:
        rows = sess.query(AlumniChangeLog).order_by(AlumniChangeLog.id.desc()).limit(n).all()
        out = [{"captured_at": r.captured_at.isoformat(), "uid": r.uid,
                "change_type": r.change_type, "field": r.field,
                "old_value": r.old_value, "new_value": r.new_value} for r in rows]
        for r in out:
            print(r)
        return out
    finally:
        sess.close()
```

- [ ] **Step 6: Run the ingest tests**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: 4 passed.

- [ ] **Step 7: Run the full test suite**

Run: `cd nes_directory && ./.venv/bin/python -m pytest -v`
Expected: all green (smoke + derive + ingest).

- [ ] **Step 8: Commit**

```bash
git add nes_directory/nes_db.py nes_directory/tests/test_ingest.py
git commit -m "feat: rewrite ingest against Postgres with derived fields + program tables"
```

---

## Task 5: Point the dashboard at Postgres

**Files:**
- Modify: `nes_directory/app.py`
- Create: `nes_directory/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `alumni_models` (Task 2), `nes_db.ingest` (Task 4).
- Produces: Flask app whose `/alumni`, `/alumni/<uid>`, `/changes`, `/` read from Postgres via `alumni_models.session_scope`. The `person.full` reads become `AlumniPerson.full` (already a dict from JSONB — no `json.loads`).

- [ ] **Step 1: Write a dashboard test**

Create `nes_directory/tests/test_dashboard.py`:

```python
import base64
import copy
import os
import pytest


REC = {
    "uid": "10", "name": "Tishin Aleksandr Vladimirovich",
    "listed_programs": ["Master of Arts in Economics [MAE]"],
    "listed_classes": ["MAE'2019"],
    "contact": {"links": [{"title": "Telegram", "url": "https://t.me/very_big_t"}]},
    "work": [{"company": "X", "position": "Lead"}],
}


@pytest.fixture()
def client(pg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("APP_PASSWORD", "secret")
    import importlib, alumni_models, nes_db, app as appmod
    importlib.reload(alumni_models)
    importlib.reload(nes_db)
    importlib.reload(appmod)
    alumni_models.init_db(alumni_models.get_engine(pg_url))
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=alumni_models.get_engine(pg_url))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}


def test_alumni_search(client):
    r = client.get("/alumni?q=Tishin", headers=_auth())
    assert r.status_code == 200 and b"Tishin" in r.data


def test_alumni_detail(client):
    r = client.get("/alumni/10", headers=_auth())
    assert r.status_code == 200 and "Lead".encode() in r.data
```

- [ ] **Step 2: Run — expect failure**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_dashboard.py -v`
Expected: FAIL — `app.py` still uses `sqlite3`/`nes_db.DB`.

- [ ] **Step 3: Replace the DB access layer in `app.py`**

In `nes_directory/app.py`, remove `import sqlite3` and the `db()` helper. Add near the top:

```python
import alumni_models as am
from alumni_models import (AlumniPerson, AlumniChangeLog, AlumniPersonHistory,
                           AlumniProgram)
from sqlalchemy import func, or_
```

Replace `dashboard_stats()`:

```python
def dashboard_stats():
    with am.session_scope() as s:
        total = s.query(AlumniPerson).count()
        active = s.query(AlumniPerson).filter(AlumniPerson.removed_at.is_(None)).count()
        changes = s.query(AlumniChangeLog).count()
        crawls = []  # crawl list optional; kept empty for now
        return {"persons": total, "active": active, "removed": total - active,
                "changes": changes, "crawls": crawls}
```

Replace `load_programs()`:

```python
def load_programs():
    with am.session_scope() as s:
        return [r.title for r in s.query(AlumniProgram).order_by(AlumniProgram.title)]
```

Replace the `alumni()` query body (the `con = db() ...` block) with:

```python
    with am.session_scope() as s:
        q = s.query(AlumniPerson)
        if not show_removed:
            q = q.filter(AlumniPerson.removed_at.is_(None))
        if query:
            q = q.filter(AlumniPerson.name.ilike(f"%{query}%"))
        if prog:
            q = q.filter(AlumniPerson.programs.cast(am.Text).ilike(f"%{prog}%"))
        total = q.count()
        rows = q.order_by(AlumniPerson.name).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all()
        people = []
        for r in rows:
            full = r.full or {}
            people.append({
                "uid": r.uid, "name": r.name, "removed": r.removed_at is not None,
                "programs": full.get("listed_programs") or [],
                "classes": full.get("listed_classes") or [],
                "residence": full.get("residence", ""),
                "position": (full.get("work") or [{}])[0].get("position", "") if full.get("work") else "",
            })
```

(Keep the existing `q`/`prog`/`page`/`pages` variable names; note the search param variable is `q` in the request and `query` in SQL — rename the request read to `query = (request.args.get("q") or "").strip()` to avoid shadowing.)

Replace the `alumni_detail()` query body:

```python
    with am.session_scope() as s:
        r = s.query(AlumniPerson).filter_by(uid=uid).first()
        if not r:
            abort(404)
        full = r.full or {}
        history = s.query(AlumniPersonHistory).filter_by(uid=uid).count()
        changes = [{"captured_at": c.captured_at.isoformat(), "change_type": c.change_type,
                    "field": c.field, "old_value": c.old_value, "new_value": c.new_value}
                   for c in s.query(AlumniChangeLog).filter_by(uid=uid)
                   .order_by(AlumniChangeLog.id.desc()).limit(100)]
        meta = {"first_seen": r.first_seen, "last_seen": r.last_seen,
                "last_changed": r.last_changed, "removed_at": r.removed_at, "versions": history}
    return render_template("alumni_detail.html", p=full, uid=uid, meta=meta, changes=changes)
```

Replace the `changes()` route body:

```python
    with am.session_scope() as s:
        rows = [{"captured_at": c.captured_at.isoformat(), "uid": c.uid, "change_type": c.change_type,
                 "field": c.field, "old_value": c.old_value, "new_value": c.new_value}
                for c in s.query(AlumniChangeLog).order_by(AlumniChangeLog.id.desc()).limit(300)]
        names = dict(s.query(AlumniPerson.uid, AlumniPerson.name).all())
    for r in rows:
        r["name"] = names.get(r["uid"], r["uid"])
    return render_template("changes.html", rows=rows)
```

In the detail template `alumni_detail.html`, the `meta.last_changed`/`removed_at` are now `datetime`; change the two slices `(meta.last_changed or '')[:10]` and `meta.removed_at[:10]` to `meta.last_changed.strftime('%Y-%m-%d') if meta.last_changed else ''` and `meta.removed_at.strftime('%Y-%m-%d')`. Same for `changes.html` where `c.captured_at[:19]|replace('T',' ')` becomes `c.captured_at[:19]|replace('T',' ')` (still a string via `.isoformat()`, so leave as-is).

- [ ] **Step 4: Run the dashboard tests**

Run: `cd nes_directory && ./.venv/bin/python -m pytest tests/test_dashboard.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite**

Run: `cd nes_directory && ./.venv/bin/python -m pytest -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add nes_directory/app.py nes_directory/templates/alumni_detail.html nes_directory/tests/test_dashboard.py
git commit -m "feat: dashboard reads alumni data from Postgres"
```

---

## Task 6: Compose Postgres + data migration

**Files:**
- Modify: `nes_directory/docker-compose.yml`
- Create: `nes_directory/MIGRATION.md`

**Interfaces:**
- Consumes: `nes_db.ingest` (Task 4).
- Produces: a local Postgres service and a documented one-shot re-ingest of the already-scraped `out/alumni.json` + `raw_html/cards/` into Postgres.

- [ ] **Step 1: Add a Postgres service to compose**

Edit `nes_directory/docker-compose.yml` to add a `db` service and wire the web to it:

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=wachter
      - POSTGRES_PASSWORD=wachter
      - POSTGRES_DB=wachter
    volumes:
      - ./pgdata:/var/lib/postgresql/data
    restart: unless-stopped

  web:
    build: .
    image: nes-alumni
    depends_on: [db]
    ports:
      - "8000:8000"
    env_file:
      - creds.env
    environment:
      - APP_USER=${APP_USER:-admin}
      - APP_PASSWORD=${APP_PASSWORD:?set APP_PASSWORD in .env before starting}
      - DATABASE_URL=postgresql://wachter:wachter@db:5432/wachter
      - NES_MIN_DELAY=${NES_MIN_DELAY:-1.0}
      - NES_MAX_DELAY=${NES_MAX_DELAY:-2.5}
    volumes:
      - ./out:/app/out
      - ./raw_html:/app/raw_html
    restart: unless-stopped
```

Add `pgdata/` to `nes_directory/.gitignore`.

- [ ] **Step 2: Write the migration note**

Create `nes_directory/MIGRATION.md`:

```markdown
# One-time: load scraped data into Postgres

Prereqs: `out/alumni.json` and `raw_html/cards/` already produced by the scraper,
and a reachable Postgres in `DATABASE_URL`.

    DATABASE_URL=postgresql://wachter:wachter@localhost:5432/wachter \
      ./.venv/bin/python -c "import nes_db; nes_db.ingest('full')"

Verify:

    DATABASE_URL=... ./.venv/bin/python -c "import nes_db; print(nes_db.stats())"
```

- [ ] **Step 3: Bring up Postgres and run the migration**

Run:
```bash
cd nes_directory
docker compose up -d db
DATABASE_URL=postgresql://wachter:wachter@localhost:5432/wachter ./.venv/bin/python -c "import nes_db; nes_db.ingest('full')"
```
Expected: `ingest[full]: seen=2905 new=2905 changed=0 removed=0`.

- [ ] **Step 4: Verify counts**

Run: `DATABASE_URL=postgresql://wachter:wachter@localhost:5432/wachter ./.venv/bin/python -c "import nes_db; print(nes_db.stats())"`
Expected: `{'persons': 2905, 'active': 2905, ...}`.

- [ ] **Step 5: Verify the dashboard against Postgres**

Run:
```bash
cd nes_directory
APP_PASSWORD=secret DATABASE_URL=postgresql://wachter:wachter@localhost:5432/wachter PORT=8077 ./.venv/bin/python app.py &
sleep 4
curl -s -u admin:secret "http://127.0.0.1:8077/alumni?q=Tishin" | grep -o "Tishin [A-Za-z]*" | head -1
kill %1
```
Expected: prints `Tishin Aleksandr`.

- [ ] **Step 6: Commit**

```bash
git add nes_directory/docker-compose.yml nes_directory/.gitignore nes_directory/MIGRATION.md
git commit -m "chore: local Postgres service + data migration runbook"
```

---

## Self-Review Notes

- **Spec coverage (Phase 1):** `alumni_person` (+derived first/last/telegram_username/grad_year_max) → Task 2/4; history/changelog/crawl → Task 4; `alumni_raw_card` → Task 4; `alumni_program(_year)` reference → Task 3/4; dashboard on Postgres → Task 5; single-Postgres compose + migration → Task 6; tests on ephemeral Postgres → Task 1 and every task. Phases 2–6 are explicitly out of scope for this plan (separate plans).
- **Types:** `ingest(kind, records, cards, engine)` used consistently across Tasks 4–6; models’ attribute names (`telegram_username`, `grad_year_max`, `full`, `removed_at`) match their uses in tests and dashboard.
- **JSONB:** `full`/`programs`/`classes` are `JSONB` → returned as Python objects (no `json.loads` in dashboard, unlike the old SQLite `TEXT`).
