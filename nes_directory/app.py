"""Flask dashboard for the NES alumni directory.

Features:
  * stats + manual triggers ("add new" / "full recrawl") with live run log
  * browse alumni cards from SQLite (search + detail, no images)
  * optional in-app cron schedule (disabled by default)

Auth: HTTP Basic via APP_USER / APP_PASSWORD env (put behind HTTPS in prod).
Run:  gunicorn -w1 --threads 8 -b 0.0.0.0:8000 app:app   (1 worker: the
      scheduler thread + file-based lock assume a single process).
"""
import os
import sys
import hmac
import json
import time
import secrets
import sqlite3
import threading
import subprocess
from datetime import datetime
from pathlib import Path

from flask import (Flask, request, Response, render_template, redirect,
                   url_for, jsonify, abort)

import nes_scraper as ns
import nes_db
import runner

HERE = Path(__file__).parent
OUT = ns.OUT
RUNS = OUT / "runs"
SCHEDULE = OUT / "schedule.json"

APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
if not APP_PASSWORD:
    # fail closed: never ship a known default password
    APP_PASSWORD = secrets.token_urlsafe(24)
    print(f"[app] ВНИМАНИЕ: APP_PASSWORD не задан — сгенерирован временный "
          f"пароль для '{APP_USER}': {APP_PASSWORD}", flush=True)
PAGE_SIZE = 24

app = Flask(__name__)


# --- auth ------------------------------------------------------------------
@app.before_request
def require_auth():
    if request.path.startswith("/healthz"):
        return None
    auth = request.authorization
    ok = (auth is not None
          and hmac.compare_digest(auth.username or "", APP_USER)
          and hmac.compare_digest(auth.password or "", APP_PASSWORD))
    if not ok:
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="NES directory"'})
    return None


# --- db helpers ------------------------------------------------------------
def db():
    con = sqlite3.connect(nes_db.DB)
    con.row_factory = sqlite3.Row
    return con


def dashboard_stats():
    if not nes_db.DB.exists():
        return {"persons": 0, "active": 0, "removed": 0, "changes": 0, "crawls": []}
    con = db()
    try:
        total = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        active = con.execute("SELECT COUNT(*) FROM person WHERE removed_at IS NULL").fetchone()[0]
        changes = con.execute("SELECT COUNT(*) FROM change_log").fetchone()[0]
        crawls = [dict(r) for r in con.execute(
            "SELECT kind,started_at,finished_at,n_seen,n_new,n_changed,n_removed "
            "FROM crawl ORDER BY id DESC LIMIT 5")]
        return {"persons": total, "active": active, "removed": total - active,
                "changes": changes, "crawls": crawls}
    finally:
        con.close()


def load_programs():
    p = OUT / "index.json"
    if not p.exists():
        return []
    progs = set()
    for v in json.loads(p.read_text(encoding="utf-8")).values():
        progs.update(v.get("programs", []))
    return sorted(progs)


# --- run status / triggers -------------------------------------------------
def tail(path, n=200):
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(data[-n:])
    except Exception:
        return ""


def launch(kind):
    if runner.is_running():
        return False, "Уже выполняется"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = RUNS / f"{ts}_{kind}.log"
    fh = open(log, "ab")
    subprocess.Popen([sys.executable, str(HERE / "runner.py"), kind, str(log)],
                     stdout=fh, stderr=subprocess.STDOUT, cwd=str(HERE),
                     start_new_session=True)
    # give runner a moment to write initial status
    time.sleep(0.4)
    return True, str(log)


@app.route("/run/<kind>", methods=["POST"])
def run_kind(kind):
    if kind not in ("new", "full"):
        abort(400)
    ok, msg = launch(kind)
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"ok": ok, "msg": msg})
    return redirect(url_for("index"))


@app.route("/status")
def status():
    st = runner.read_status()
    st["running"] = runner.is_running()
    st["log_tail"] = tail(st.get("log"), 200) if st.get("log") else ""
    return jsonify(st)


@app.route("/healthz")
def healthz():
    return "ok"


# --- schedule --------------------------------------------------------------
def read_schedule():
    if SCHEDULE.exists():
        try:
            return json.loads(SCHEDULE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": False, "kind": "new", "cron": "0 4 * * 1", "last_run": None}


def write_schedule(cfg):
    SCHEDULE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/schedule", methods=["POST"])
def save_schedule():
    cfg = read_schedule()
    cfg["enabled"] = request.form.get("enabled") == "on"
    cfg["kind"] = request.form.get("kind", "new")
    cfg["cron"] = request.form.get("cron", "0 4 * * 1").strip()
    # validate cron if possible
    try:
        from croniter import croniter
        croniter(cfg["cron"], datetime.now())
        cfg["cron_valid"] = True
    except Exception as e:
        cfg["cron_valid"] = False
        cfg["cron_error"] = str(e)
    write_schedule(cfg)
    return redirect(url_for("index"))


def scheduler_loop():
    """Background ticker: when enabled, fire the configured run after each cron
    instant passes. The runner lock prevents overlap. Disabled by default."""
    try:
        from croniter import croniter
    except Exception:
        print("[scheduler] croniter не установлен — расписание выключено")
        return
    while True:
        time.sleep(30)
        cfg = read_schedule()
        if not cfg.get("enabled"):
            continue
        try:
            now = datetime.now()
            base = cfg.get("last_run")
            base_dt = datetime.fromisoformat(base) if base else now
            prev = croniter(cfg["cron"], now).get_prev(datetime)
            if prev > base_dt and not runner.is_running():
                print(f"[scheduler] запуск '{cfg['kind']}' по расписанию {cfg['cron']}")
                launch(cfg["kind"])
                cfg["last_run"] = now.isoformat(timespec="seconds")
                write_schedule(cfg)
        except Exception as e:
            print("[scheduler] ошибка:", e)


_scheduler_started = False


def start_scheduler_once():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=scheduler_loop, daemon=True).start()


# --- alumni browse ---------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", stats=dashboard_stats(),
                           schedule=read_schedule(), status=runner.read_status())


@app.route("/alumni")
def alumni():
    q = (request.args.get("q") or "").strip()
    prog = (request.args.get("program") or "").strip()
    show_removed = request.args.get("removed") == "1"
    page = max(1, int(request.args.get("page", 1)))
    where, params = [], []
    if not show_removed:
        where.append("removed_at IS NULL")
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    if prog:
        where.append("full LIKE ?")
        params.append(f"%{prog}%")
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    con = db()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM person {wsql}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT uid,name,full,removed_at,last_changed FROM person {wsql} "
            f"ORDER BY name LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    finally:
        con.close()
    people = []
    for r in rows:
        full = json.loads(r["full"]) if r["full"] else {}
        people.append({
            "uid": r["uid"], "name": r["name"],
            "removed": bool(r["removed_at"]),
            "programs": full.get("listed_programs") or [],
            "classes": full.get("listed_classes") or [],
            "residence": full.get("residence", ""),
            "position": (full.get("work") or [{}])[0].get("position", "") if full.get("work") else "",
        })
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template("alumni_list.html", people=people, total=total,
                           page=page, pages=pages, q=q, program=prog,
                           programs=load_programs(), show_removed=show_removed)


@app.route("/alumni/<uid>")
def alumni_detail(uid):
    con = db()
    try:
        row = con.execute("SELECT uid,name,full,first_seen,last_seen,last_changed,removed_at "
                          "FROM person WHERE uid=?", (uid,)).fetchone()
        history = con.execute("SELECT captured_at,content_hash FROM person_history "
                              "WHERE uid=? ORDER BY id DESC", (uid,)).fetchall()
        changes = con.execute("SELECT captured_at,change_type,field,old_value,new_value "
                              "FROM change_log WHERE uid=? ORDER BY id DESC LIMIT 100", (uid,)).fetchall()
    finally:
        con.close()
    if not row:
        abort(404)
    full = json.loads(row["full"]) if row["full"] else {}
    meta = {"first_seen": row["first_seen"], "last_seen": row["last_seen"],
            "last_changed": row["last_changed"], "removed_at": row["removed_at"],
            "versions": len(history)}
    return render_template("alumni_detail.html", p=full, uid=uid, meta=meta,
                           changes=[dict(c) for c in changes])


@app.route("/changes")
def changes():
    con = db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT captured_at,uid,change_type,field,old_value,new_value "
            "FROM change_log ORDER BY id DESC LIMIT 300")]
        names = dict(con.execute("SELECT uid,name FROM person").fetchall())
    finally:
        con.close()
    for r in rows:
        r["name"] = names.get(r["uid"], r["uid"])
    return render_template("changes.html", rows=rows)


start_scheduler_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
