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
                           AlumniProgram, AlumniProgramYear, AlumniCrawl, TgIdentity)
from sqlalchemy import func, or_, text as sa_text

import nes_scraper as ns
import nes_db
import runner
import alumni_link
import translit

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


def like_escape(term):
    """Экранирует LIKE-метасимволы, чтобы пользовательский ввод в поиске не
    работал как шаблон (%/_). Использовать с ilike/like(..., escape='\\\\')."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def load_chats():
    """Чаты, которыми управляет бот (id + название) — для селектора участников.
    Таблица chats принадлежит боту; своя сессия, чтобы её отсутствие не роняло
    страницу."""
    try:
        with am.session_scope() as s:
            return [(r[0], r[1] or str(r[0])) for r in s.execute(
                sa_text("SELECT id, title FROM chats ORDER BY title NULLS LAST, id"))]
    except Exception:
        return []


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
    year = (request.args.get("year") or "").strip()
    show_removed = request.args.get("removed") == "1"
    page = max(1, int(request.args.get("page", 1)))
    with am.session_scope() as s:
        q = s.query(AlumniPerson)
        if not show_removed:
            q = q.filter(AlumniPerson.removed_at.is_(None))
        if query:
            # имя ищем на обоих языках (рус → транслит на латиницу базы);
            # город/страну — по сырому запросу в тексте карточки
            ql = like_escape(query)
            conds = [AlumniPerson.name.ilike(f"%{ql}%", escape="\\"),
                     AlumniPerson.full.cast(am.Text).ilike(f"%{ql}%", escape="\\")]
            if translit.has_cyrillic(query):
                conds.append(AlumniPerson.name.ilike(
                    f"%{like_escape(translit.ru_to_lat(query))}%", escape="\\"))
            q = q.filter(or_(*conds))
        if prog:
            q = q.filter(AlumniPerson.programs.cast(am.Text).ilike(
                f"%{like_escape(prog)}%", escape="\\"))
        if year.isdigit():
            # любой класс с этим годом (учитывает несколько программ)
            q = q.filter(AlumniPerson.classes.cast(am.Text).like(f"%'{year}%"))
        total = q.count()
        rows = q.order_by(AlumniPerson.name).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all()
        years = [str(y) for (y,) in s.query(AlumniPerson.grad_year_max).distinct()
                 .filter(AlumniPerson.grad_year_max.isnot(None))
                 .order_by(AlumniPerson.grad_year_max.desc())]
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
                           page=page, pages=pages, q=query, program=prog, year=year,
                           years=years, programs=load_programs(), show_removed=show_removed)


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
    prog = (request.args.get("program") or "").strip()
    year = (request.args.get("year") or "").strip()
    q = (request.args.get("q") or "").strip()
    chat = (request.args.get("chat") or "").strip()
    page = max(1, int(request.args.get("page", 1)))
    chats = load_chats()   # своя сессия — отсутствие таблицы не ломает страницу
    with am.session_scope() as s:
        query = s.query(TgIdentity)
        if cat:
            query = query.filter(TgIdentity.category == cat)
        if q:
            like = f"%{like_escape(q)}%"
            query = query.filter(or_(TgIdentity.username.ilike(like, escape="\\"),
                                     TgIdentity.declared_name.ilike(like, escape="\\")))
        if chat:   # участники конкретного чата — по строкам users(chat_id, user_id)
            cid = int(chat) if chat.lstrip("-").isdigit() else None
            member_ids = [] if cid is None else [r[0] for r in s.execute(
                sa_text("SELECT user_id FROM users WHERE chat_id = :cid"), {"cid": cid})]
            query = query.filter(TgIdentity.user_id.in_(member_ids))
        # программа/год: у выпускника — по привязанной карточке (код класса),
        # у студента/ненайденного — по заявленным полям
        if prog or year.isdigit():
            if cat == "alumni":
                query = query.join(AlumniPerson, TgIdentity.alumni_uid == AlumniPerson.uid)
                if prog:
                    query = query.filter(AlumniPerson.classes.cast(am.Text).ilike(
                        f"%{like_escape(prog)}'%", escape="\\"))
                if year.isdigit():
                    query = query.filter(AlumniPerson.classes.cast(am.Text).like(f"%'{year}%"))
            else:
                if prog:
                    query = query.filter(TgIdentity.declared_program == prog)
                if year.isdigit():
                    query = query.filter(TgIdentity.declared_year == int(year))
        total = query.count()
        rows = (query.order_by(TgIdentity.category, TgIdentity.username)
                .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all())
        counts = dict(s.query(TgIdentity.category, func.count())
                      .group_by(TgIdentity.category).all())
        prog_codes = sorted({c for (c,) in s.query(AlumniProgramYear.program_code).distinct()})
        alum_years = {y for (y,) in s.query(AlumniPerson.grad_year_max).distinct()
                      if y is not None}
        decl_years = {y for (y,) in s.query(TgIdentity.declared_year).distinct()
                      if y is not None}
        years = [str(y) for y in sorted(alum_years | decl_years, reverse=True)]
        uids = [r.alumni_uid for r in rows if r.alumni_uid]
        alum = {a.uid: a for a in s.query(AlumniPerson).filter(AlumniPerson.uid.in_(uids))} if uids else {}
        people = []
        for r in rows:
            card = {"user_id": r.user_id, "username": r.username, "category": r.category,
                    "alumni_uid": r.alumni_uid}
            a = alum.get(r.alumni_uid)
            if a is not None:
                full = a.full or {}
                work = full.get("work") or []
                card.update(name=a.name, classes=full.get("listed_classes") or [],
                            info=(work[0].get("position", "") if work else ""),
                            residence=full.get("residence", ""))
            else:
                nm = r.declared_name or (f"@{r.username}" if r.username else str(r.user_id))
                cls = [f"{r.declared_program}'{r.declared_year}"] if r.declared_program else \
                      ([r.declared_program] if r.declared_program else [])
                card.update(name=nm, classes=cls, info=r.intro or "", residence="")
            people.append(card)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template("identities_list.html", people=people, total=total, page=page,
                           pages=pages, q=q, category=cat, program=prog, year=year,
                           programs=prog_codes, years=years,
                           categories=IDENTITY_CATEGORIES, counts=counts,
                           chats=chats, chat=chat)


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
            conds = [AlumniPerson.name.ilike(f"%{like_escape(aq)}%", escape="\\")]
            if translit.has_cyrillic(aq):   # искать латинское имя по русскому вводу
                conds.append(AlumniPerson.name.ilike(
                    f"%{like_escape(translit.ru_to_lat(aq))}%", escape="\\"))
            for a in (s.query(AlumniPerson).filter(or_(*conds))
                      .order_by(AlumniPerson.name).limit(20)):
                full = a.full or {}
                work = full.get("work") or []
                candidates.append({
                    "uid": a.uid, "name": a.name, "classes": a.classes or [],
                    "telegram": a.telegram_username, "emails": a.emails or [],
                    "residence": full.get("residence", ""),
                    "position": work[0].get("position", "") if work else ""})
    return render_template("identity_detail.html", i=data, linked=linked,
                           candidates=candidates, aq=aq, categories=IDENTITY_CATEGORIES)


@app.route("/identities/<int:user_id>/delete", methods=["POST"])
def identity_delete(user_id):
    with am.session_scope() as s:
        ident = s.get(TgIdentity, user_id)
        if ident:
            s.delete(ident)
        # чистим и per-chat whois в users (иначе бот при перезаходе даст «Снова»)
        s.execute(sa_text("DELETE FROM users WHERE user_id = :uid"), {"uid": user_id})
    return redirect(url_for("identities"))


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


@app.route("/identities/<int:user_id>/edit", methods=["POST"])
def identity_edit(user_id):
    """Ручное редактирование заявленных данных участника (имя/о себе/программа/
    год/email) — в т.ч. чтобы развести совпадающие «Заявленное имя» и «О себе»."""
    with am.session_scope() as s:
        ident = s.get(TgIdentity, user_id)
        if not ident:
            abort(404)
        ident.declared_name = (request.form.get("declared_name") or "").strip() or None
        ident.intro = (request.form.get("intro") or "").strip() or None
        ident.declared_program = (request.form.get("declared_program") or "").strip() or None
        yr = (request.form.get("declared_year") or "").strip()
        ident.declared_year = int(yr) if yr.isdigit() else None
        ident.declared_email = (request.form.get("declared_email") or "").strip() or None
    return redirect(url_for("identity_detail", user_id=user_id))


# создаём общую схему (alumni_* / tg_identity) на старте, чтобы свежий деплой
# показывал пустое состояние, а не 500 до первого bootstrap. Идемпотентно.
try:
    am.init_db()
except Exception as e:
    print(f"[startup] init_db пропущен: {e}")

start_scheduler_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
