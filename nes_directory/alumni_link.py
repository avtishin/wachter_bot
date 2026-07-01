"""Linking logic between Telegram identities and alumni records.

Pure-ish helpers over a SQLAlchemy session: find an alumnus by telegram
username or email, classify a self-declared newcomer, upsert a `tg_identity`,
and reconcile unresolved identities against the (possibly updated) directory.
Shared by the members.csv seed, the dashboard resolve, and (later) the bot.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import func

import alumni_models as m

_TG_RE = re.compile(r"(?:t|telegram)\.me/([A-Za-z0-9_]+)")

# categories that might still turn out to be an alumnus and are re-checked on
# each reconcile; `friend`/`employee`/`alumni` are left alone.
RECONCILABLE = ["student", "unresolved_alumni", "unknown"]


def _now():
    return datetime.now(timezone.utc)


def normalize_username(u):
    if not u:
        return None
    u = u.strip()
    mm = _TG_RE.search(u)
    if mm:
        u = mm.group(1)
    u = u.lstrip("@").strip().lower()
    return u or None


def normalize_email(e):
    if not e:
        return None
    e = e.strip().lower()
    return e or None


def find_by_username(session, username):
    un = normalize_username(username)
    if not un:
        return None
    return (session.query(m.AlumniPerson)
            .filter(func.lower(m.AlumniPerson.telegram_username) == un,
                    m.AlumniPerson.removed_at.is_(None))
            .first())


def find_by_email(session, email):
    em = normalize_email(email)
    if not em:
        return None
    return (session.query(m.AlumniPerson)
            .filter(m.AlumniPerson.emails.contains([em]),
                    m.AlumniPerson.removed_at.is_(None))
            .first())


def max_grad_year(session):
    return session.query(func.max(m.AlumniPerson.grad_year_max)).scalar()


def classify(choice, year, max_year):
    """Category for a newcomer not auto-matched in the directory.

    choice: 'alumnus' | 'student' | 'friend' | 'employee'
    """
    if choice == "student":
        return "student"
    if choice in ("friend", "employee"):
        return choice
    # declared alumnus but not found by username/email:
    if year is not None and max_year is not None and year > max_year:
        return "student"            # claims a class newer than we have
    return "unresolved_alumni"      # should be in the base -> resolve later


def upsert_identity(session, user_id, **fields):
    """Create or update a tg_identity. `username`/`declared_email` are
    normalized; on update, only non-None fields overwrite."""
    if "username" in fields:
        fields["username"] = normalize_username(fields["username"])
    if "declared_email" in fields:
        fields["declared_email"] = normalize_email(fields["declared_email"])
    ts = _now()
    ident = session.get(m.TgIdentity, user_id)
    if ident is None:
        ident = m.TgIdentity(user_id=user_id, first_seen=ts, last_seen=ts)
        for k, v in fields.items():
            setattr(ident, k, v)
        session.add(ident)
    else:
        for k, v in fields.items():
            if v is not None:
                setattr(ident, k, v)
        ident.last_seen = ts
    if fields.get("category") == "alumni" and ident.verified_at is None:
        ident.verified_at = ts
    session.commit()
    return ident


def link_alumnus(session, ident, alum):
    ident.alumni_uid = alum.uid
    ident.category = "alumni"
    ident.verified_at = _now()


def reconcile(session):
    """Re-check unresolved identities against the directory (by username, then
    declared email). Returns {'linked': n}."""
    linked = 0
    rows = (session.query(m.TgIdentity)
            .filter(m.TgIdentity.alumni_uid.is_(None),
                    m.TgIdentity.category.in_(RECONCILABLE))
            .all())
    for ident in rows:
        alum = (find_by_username(session, ident.username)
                or find_by_email(session, ident.declared_email))
        if alum:
            link_alumnus(session, ident, alum)
            linked += 1
    session.commit()
    return {"linked": linked}
