"""Pipeline orchestrator for the web UI (and CLI).

Runs the whole refresh in one process, writing machine-readable status to
out/job_status.json and a per-run log, guarded by a lock so only one run
happens at a time. The web app launches this as a subprocess; it can also be
run by hand:

    python3 runner.py new      # add new alumni (skip already-downloaded cards)
    python3 runner.py full     # full recrawl (refetch every card -> catch edits)

Status state machine: idle -> running -> finished | failed.
"""
import os
import sys
import json
import time
import pathlib
from datetime import datetime, timezone

import nes_scraper as ns
import nes_db

OUT = ns.OUT
RUNS = OUT / "runs"
RUNS.mkdir(parents=True, exist_ok=True)
STATUS = OUT / "job_status.json"
LOCK = OUT / "job.lock"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_status():
    if STATUS.exists():
        try:
            return json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {"state": "idle"}


def write_status(**fields):
    cur = read_status()
    cur.update(fields)
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS)
    return cur


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def is_running():
    st = read_status()
    return st.get("state") == "running" and pid_alive(st.get("pid"))


def _set_phase(phase):
    write_status(phase=phase, phase_at=_now())
    print(f"[runner] phase={phase}", flush=True)


def run(kind, log_path=None):
    if kind not in ("new", "full"):
        sys.exit(f"unknown kind {kind!r} (use new|full)")
    if is_running():
        print("[runner] другой ран уже идёт — выхожу")
        return 1

    write_status(state="running", kind=kind, pid=os.getpid(),
                 started_at=_now(), finished_at=None, phase="start",
                 error=None, log=str(log_path) if log_path else None,
                 result=None)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")

    try:
        s = ns.make_session()
        u, p = ns.load_creds()
        ns.login(s, u, p)
        print("[runner] залогинены", flush=True)

        _set_phase("index")
        index = ns.build_index(s)

        if kind == "full":
            os.environ["NES_REFRESH"] = "1"   # refetch every card
        _set_phase("download")
        ns.download_cards(s, index)

        _set_phase("parse")
        ns.parse_all(index)

        _set_phase("ingest")
        nes_db.ingest("full" if kind == "full" else "new")

        st = nes_db_counts()
        write_status(state="finished", finished_at=_now(), phase="done",
                     result=st)
        print(f"[runner] готово: {st}", flush=True)
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        write_status(state="failed", finished_at=_now(), error=repr(e))
        return 2
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


def nes_db_counts():
    from sqlalchemy.orm import sessionmaker
    from alumni_models import get_engine, AlumniPerson, AlumniCrawl
    sess = sessionmaker(bind=get_engine())()
    try:
        total = sess.query(AlumniPerson).count()
        active = sess.query(AlumniPerson).filter(AlumniPerson.removed_at.is_(None)).count()
        last = sess.query(AlumniCrawl).order_by(AlumniCrawl.id.desc()).first()
        return {"persons": total, "active": active,
                "last_crawl": {"kind": last.kind, "seen": last.n_seen, "new": last.n_new,
                               "changed": last.n_changed, "removed": last.n_removed}
                if last else None}
    finally:
        sess.close()


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "new"
    log_path = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(run(kind, log_path))
