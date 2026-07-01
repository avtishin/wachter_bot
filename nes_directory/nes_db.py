"""SQLite store for the NES alumni directory with full version history.

Ingests parsed records (out/alumni.json) into out/nes.db:

  person          current snapshot per uid (+ first_seen/last_seen/hash/removed)
  person_history  append-only: one row per distinct content version
  change_log      field-level diffs (created / added / removed / changed)
  crawl           one row per ingest run with counters

Change detection is content-hash based: each record is canonicalised
(meaningful fields only, lists sorted) and sha256'd. A changed hash triggers a
deep field-level diff against the previous version. Nothing is deleted —
people missing from a fresh full index get removed_at set, keeping history.

Usage:
  python3 nes_db.py ingest [--kind full|new]   # read out/alumni.json + index
  python3 nes_db.py stats
  python3 nes_db.py changes [N]                 # last N change_log rows
"""
import os
import sys
import json
import time
import hashlib
import sqlite3
import pathlib
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).parent
OUT = HERE / "out"
DB = OUT / "nes.db"

# fields that define a person's "content" for change detection (order matters
# only for readability; lists are canonicalised below)
CONTENT_FIELDS = [
    "name", "sex", "birthday", "residence", "photo_url",
    "contact", "education", "education_after_nes", "work", "teaching",
    "interests", "expertise", "class_leader",
    "listed_programs", "listed_classes", "other_sections",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS person (
    uid          TEXT PRIMARY KEY,
    name         TEXT,
    content_hash TEXT,
    data         TEXT,          -- canonical JSON (used for change detection)
    full         TEXT,          -- full display JSON (parsed record, original order)
    first_seen   TEXT,
    last_seen    TEXT,
    last_changed TEXT,
    removed_at   TEXT
);
CREATE TABLE IF NOT EXISTS person_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    uid          TEXT,
    content_hash TEXT,
    captured_at  TEXT,
    data         TEXT
);
CREATE INDEX IF NOT EXISTS ix_hist_uid ON person_history(uid);
CREATE TABLE IF NOT EXISTS change_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    uid          TEXT,
    captured_at  TEXT,
    change_type  TEXT,          -- created | added | removed | changed
    field        TEXT,
    old_value    TEXT,
    new_value    TEXT
);
CREATE INDEX IF NOT EXISTS ix_chg_uid ON change_log(uid);
CREATE TABLE IF NOT EXISTS crawl (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT,
    finished_at  TEXT,
    kind         TEXT,
    n_seen       INTEGER,
    n_new        INTEGER,
    n_changed    INTEGER,
    n_removed    INTEGER
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    OUT.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    # migrate older DBs that predate the `full` column
    cols = {r[1] for r in con.execute("PRAGMA table_info(person)")}
    if "full" not in cols:
        con.execute("ALTER TABLE person ADD COLUMN full TEXT")
        con.commit()
    return con


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


# --- ingest ----------------------------------------------------------------
def ingest(kind="full"):
    alumni_path = OUT / "alumni.json"
    if not alumni_path.exists():
        sys.exit("Нет out/alumni.json — сначала запусти фазу parse.")
    records = json.loads(alumni_path.read_text(encoding="utf-8"))

    con = connect()
    cur = con.cursor()
    started = now()
    seen_uids = set()
    n_new = n_changed = 0

    for rec in records:
        uid = str(rec["uid"])
        seen_uids.add(uid)
        canon = canonical(rec)
        h = content_hash(canon)
        canon_json = json.dumps(canon, ensure_ascii=False, sort_keys=True)
        full_json = json.dumps({k: v for k, v in rec.items() if not k.startswith("_")},
                               ensure_ascii=False)
        name = rec.get("name") or rec.get("listed_name") or ""

        row = cur.execute("SELECT content_hash, data, removed_at FROM person WHERE uid=?",
                          (uid,)).fetchone()
        ts = now()
        if row is None:
            cur.execute(
                "INSERT INTO person(uid,name,content_hash,data,full,first_seen,last_seen,last_changed,removed_at)"
                " VALUES(?,?,?,?,?,?,?,?,NULL)",
                (uid, name, h, canon_json, full_json, ts, ts, ts))
            cur.execute("INSERT INTO person_history(uid,content_hash,captured_at,data) VALUES(?,?,?,?)",
                        (uid, h, ts, canon_json))
            cur.execute("INSERT INTO change_log(uid,captured_at,change_type,field,old_value,new_value)"
                        " VALUES(?,?,?,?,?,?)", (uid, ts, "created", "", None, name))
            n_new += 1
        else:
            old_hash, old_data, removed_at = row
            if old_hash != h:
                old_canon = json.loads(old_data) if old_data else {}
                for ctype, field, ov, nv in diff(old_canon, canon):
                    cur.execute(
                        "INSERT INTO change_log(uid,captured_at,change_type,field,old_value,new_value)"
                        " VALUES(?,?,?,?,?,?)", (uid, ts, ctype, field, ov, nv))
                cur.execute("INSERT INTO person_history(uid,content_hash,captured_at,data) VALUES(?,?,?,?)",
                            (uid, h, ts, canon_json))
                cur.execute("UPDATE person SET name=?,content_hash=?,data=?,full=?,last_seen=?,last_changed=?,removed_at=NULL"
                            " WHERE uid=?", (name, h, canon_json, full_json, ts, ts, uid))
                n_changed += 1
            else:
                cur.execute("UPDATE person SET full=?,last_seen=?,removed_at=NULL WHERE uid=?",
                            (full_json, ts, uid))

    # mark removed (only meaningful on a full crawl where `records` = whole roster)
    n_removed = 0
    if kind == "full":
        ts = now()
        for (uid,) in cur.execute("SELECT uid FROM person WHERE removed_at IS NULL").fetchall():
            if uid not in seen_uids:
                cur.execute("UPDATE person SET removed_at=? WHERE uid=?", (ts, uid))
                cur.execute("INSERT INTO change_log(uid,captured_at,change_type,field,old_value,new_value)"
                            " VALUES(?,?,?,?,?,?)", (uid, ts, "removed", "", "present", None))
                n_removed += 1

    cur.execute("INSERT INTO crawl(started_at,finished_at,kind,n_seen,n_new,n_changed,n_removed)"
                " VALUES(?,?,?,?,?,?,?)",
                (started, now(), kind, len(seen_uids), n_new, n_changed, n_removed))
    con.commit()
    con.close()
    print(f"ingest[{kind}]: seen={len(seen_uids)} new={n_new} changed={n_changed} removed={n_removed} -> {DB}")


def stats():
    con = connect(); cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    active = cur.execute("SELECT COUNT(*) FROM person WHERE removed_at IS NULL").fetchone()[0]
    hist = cur.execute("SELECT COUNT(*) FROM person_history").fetchone()[0]
    chg = cur.execute("SELECT COUNT(*) FROM change_log").fetchone()[0]
    print(f"persons: {total} (active {active}) | history rows: {hist} | change_log: {chg}")
    for r in cur.execute("SELECT id,kind,started_at,n_seen,n_new,n_changed,n_removed FROM crawl ORDER BY id DESC LIMIT 5"):
        print("  crawl", r)
    con.close()


def changes(n=30):
    con = connect(); cur = con.cursor()
    for r in cur.execute("SELECT captured_at,uid,change_type,field,old_value,new_value"
                         " FROM change_log ORDER BY id DESC LIMIT ?", (n,)):
        print(r)
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ingest"
    if cmd == "ingest":
        kind = "full"
        if "--kind" in sys.argv:
            kind = sys.argv[sys.argv.index("--kind") + 1]
        ingest(kind)
    elif cmd == "stats":
        stats()
    elif cmd == "changes":
        changes(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        sys.exit(f"unknown command {cmd!r}")
