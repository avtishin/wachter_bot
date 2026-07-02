"""Flask dashboard for the NES alumni directory.

Features:
  * stats + manual triggers ("add new" / "full recrawl") with live run log
  * browse alumni cards from Postgres (search + detail, no images)
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
import threading
import subprocess
from datetime import datetime
from pathlib import Path

from flask import (Flask, request, Response, render_template, redirect,
                   url_for, jsonify, abort)

import alumni_models as am
from alumni_models import (AlumniPerson, AlumniChangeLog, AlumniPersonHistory,
                           AlumniProgram, AlumniCrawl, TgIdentity)
from sqlalchemy import func, or_

import nes_scraper as ns
import nes_db
import runner
import alumni_link

IDENTITY_CATEGORIES = ["alumni", "student", "unresolved_alumni",
                       "friend", "employee", "unknown"]

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
def dashboard_stats():
    with am.session_scope() as s:
        total = s.query(AlumniPerson).count()
        active = s.query(AlumniPerson).filter(AlumniPerson.removed_at.is_(None)).count()
        changes = s.query(AlumniChangeLog).count()
        crawls = [{
            "kind": c.kind,
            "started_at": c.started_at.strftime("%Y-%m-%d %H:%M") if c.started_at else "",
            "finished_at": c.finished_at.strftime("%H:%M") if c.finished_at else "…",
            "n_seen": c.n_seen, "n_new": c.n_new,
            "n_changed": c.n_changed, "n_removed": c.n_removed,
        } for c in s.query(AlumniCrawl).order_by(AlumniCrawl.id.desc()).limit(5)]
        return {"persons": total, "active": active, "removed": total - active,
                "changes": changes, "crawls": crawls}


def load_programs():
    with am.session_scope() as s:
        return [r.title for r in s.query(AlumniProgram).order_by(AlumniProgram.title)]


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
    query = (request.args.get("q") or "").strip()
    prog = (request.args.get("program") or "").strip()
    show_removed = request.args.get("removed") == "1"
    page = max(1, int(request.args.get("page", 1)))
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
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template("alumni_list.html", people=people, total=total,
                           page=page, pages=pages, q=query, program=prog,
                           programs=load_programs(), show_removed=show_removed)


@app.route("/alumni/<uid>")
def alumni_detail(uid):
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


@app.route("/changes")
def changes():
    with am.session_scope() as s:
        rows = [{"captured_at": c.captured_at.isoformat(), "uid": c.uid, "change_type": c.change_type,
                 "field": c.field, "old_value": c.old_value, "new_value": c.new_value}
                for c in s.query(AlumniChangeLog).order_by(AlumniChangeLog.id.desc()).limit(300)]
        names = dict(s.query(AlumniPerson.uid, AlumniPerson.name).all())
    for r in rows:
        r["name"] = names.get(r["uid"], r["uid"])
    return render_template("changes.html", rows=rows)


# --- telegram identities ---------------------------------------------------
@app.route("/identities")
def identities():
    cat = (request.args.get("category") or "").strip()
    q = (request.args.get("q") or "").strip()
    page = max(1, int(request.args.get("page", 1)))
    with am.session_scope() as s:
        query = s.query(TgIdentity)
        if cat:
            query = query.filter(TgIdentity.category == cat)
        if q:
            like = f"%{q}%"
            query = query.filter(or_(TgIdentity.username.ilike(like),
                                     TgIdentity.declared_name.ilike(like)))
        total = query.count()
        rows = (query.order_by(TgIdentity.category, TgIdentity.username)
                .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all())
        counts = dict(s.query(TgIdentity.category, func.count())
                      .group_by(TgIdentity.category).all())
        uids = [r.alumni_uid for r in rows if r.alumni_uid]
        names = (dict(s.query(AlumniPerson.uid, AlumniPerson.name)
                      .filter(AlumniPerson.uid.in_(uids)).all()) if uids else {})
        people = [{"user_id": r.user_id, "username": r.username, "category": r.category,
                   "alumni_uid": r.alumni_uid, "alumni_name": names.get(r.alumni_uid),
                   "declared_name": r.declared_name, "declared_program": r.declared_program,
                   "declared_year": r.declared_year, "declared_email": r.declared_email}
                  for r in rows]
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template("identities_list.html", people=people, total=total, page=page,
                           pages=pages, q=q, category=cat,
                           categories=IDENTITY_CATEGORIES, counts=counts)


@app.route("/identities/<int:user_id>")
def identity_detail(user_id):
    aq = (request.args.get("aq") or "").strip()
    with am.session_scope() as s:
        ident = s.get(TgIdentity, user_id)
        if not ident:
            abort(404)
        data = {"user_id": ident.user_id, "username": ident.username, "category": ident.category,
                "alumni_uid": ident.alumni_uid, "declared_name": ident.declared_name,
                "declared_program": ident.declared_program, "declared_year": ident.declared_year,
                "declared_email": ident.declared_email, "intro": ident.intro, "source": ident.source}
        linked = None
        if ident.alumni_uid:
            a = s.get(AlumniPerson, ident.alumni_uid)
            linked = {"uid": a.uid, "name": a.name} if a else None
        candidates = []
        if aq:
            like = f"%{aq}%"
            for a in (s.query(AlumniPerson).filter(AlumniPerson.name.ilike(like))
                      .order_by(AlumniPerson.name).limit(20)):
                candidates.append({"uid": a.uid, "name": a.name, "classes": a.classes or [],
                                   "telegram": a.telegram_username, "emails": a.emails or []})
    return render_template("identity_detail.html", i=data, linked=linked,
                           candidates=candidates, aq=aq, categories=IDENTITY_CATEGORIES)


@app.route("/identities/<int:user_id>/resolve", methods=["POST"])
def identity_resolve(user_id):
    uid = (request.form.get("alumni_uid") or "").strip()
    with am.session_scope() as s:
        ident = s.get(TgIdentity, user_id)
        if not ident:
            abort(404)
        alum = s.get(AlumniPerson, uid) if uid else None
        if alum:
            alumni_link.link_alumnus(s, ident, alum)
    return redirect(url_for("identity_detail", user_id=user_id))


@app.route("/identities/<int:user_id>/category", methods=["POST"])
def identity_category(user_id):
    cat = (request.form.get("category") or "").strip()
    with am.session_scope() as s:
        ident = s.get(TgIdentity, user_id)
        if not ident:
            abort(404)
        if cat in IDENTITY_CATEGORIES:
            ident.category = cat
            if cat != "alumni":
                ident.alumni_uid = None
    return redirect(url_for("identity_detail", user_id=user_id))


start_scheduler_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
