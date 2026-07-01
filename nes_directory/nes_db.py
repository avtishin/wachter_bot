"""Postgres store for the NES alumni directory with full version history.

Ingests parsed records into Postgres via SQLAlchemy:

  alumni_person          current snapshot per uid (+ first_seen/last_seen/hash/removed)
  alumni_person_history  append-only: one row per distinct content version
  alumni_change_log      field-level diffs (created / added / removed / changed)
  alumni_crawl           one row per ingest run with counters
  alumni_raw_card        raw HTML per uid
  alumni_program         program code → title
  alumni_program_year    program code × grad year pairs

Change detection is content-hash based: each record is canonicalised
(meaningful fields only, lists sorted) and sha256'd. A changed hash triggers a
deep field-level diff against the previous version. Nothing is deleted —
people missing from a fresh full index get removed_at set, keeping history.

Usage:
  python3 nes_db.py ingest [--kind full|new]   # read out/alumni.json + raw_html/cards/
  python3 nes_db.py stats
  python3 nes_db.py changes [N]                 # last N change_log rows
"""
import os
import sys
import json
import hashlib
import pathlib
from datetime import datetime, timezone

import alumni_models as m
import alumni_derive as d
from alumni_models import get_engine, init_db, AlumniPerson, AlumniPersonHistory, \
    AlumniChangeLog, AlumniCrawl, AlumniRawCard, AlumniProgram, AlumniProgramYear
from sqlalchemy.orm import sessionmaker

HERE = pathlib.Path(__file__).parent
OUT = HERE / "out"

# fields that define a person's "content" for change detection (order matters
# only for readability; lists are canonicalised below)
CONTENT_FIELDS = [
    "name", "sex", "birthday", "residence", "photo_url",
    "contact", "education", "education_after_nes", "work", "teaching",
    "interests", "expertise", "class_leader",
    "listed_programs", "listed_classes", "other_sections",
]


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


# --- canonicalisation & hashing -------------------------------------------
def _canon(value):
    """Deterministic structure: sort lists of dicts/scalars stably."""
    if isinstance(value, dict):
        return {k: _canon(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        items = [_canon(v) for v in value]
        # sort by canonical JSON so order changes don't look like edits
        return sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    return value


def canonical(rec):
    body = {k: rec.get(k) for k in CONTENT_FIELDS if rec.get(k) not in (None, "", [], {})}
    return _canon(body)


def content_hash(canon):
    blob = json.dumps(canon, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- deep field-level diff -------------------------------------------------
def _flatten(obj, prefix=""):
    """Flatten nested dict/list into {dotted.path: scalar-or-jsonblob}."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        # represent each list element by its canonical json (stable, since
        # _canon already sorted them) so add/remove is detectable by value
        for v in obj:
            key = f"{prefix}[]={json.dumps(v, ensure_ascii=False, sort_keys=True)}"
            out[key] = True
    else:
        out[prefix] = obj
    return out


def diff(old_canon, new_canon):
    """Yield (change_type, field, old_value, new_value)."""
    of, nf = _flatten(old_canon), _flatten(new_canon)
    okeys, nkeys = set(of), set(nf)
    for k in sorted(nkeys - okeys):
        yield ("added", k, None, _as_text(nf[k]))
    for k in sorted(okeys - nkeys):
        yield ("removed", k, _as_text(of[k]), None)
    for k in sorted(okeys & nkeys):
        if of[k] != nf[k]:
            yield ("changed", k, _as_text(of[k]), _as_text(nf[k]))


def _as_text(v):
    if v is True:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


# --- session factory -------------------------------------------------------
def _session_factory(engine):
    return sessionmaker(bind=engine or get_engine())


# --- ingest ----------------------------------------------------------------
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
