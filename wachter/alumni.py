"""Alumni recognition for the bot.

Looks a joining Telegram user up against the shared directory tables and
formats greetings. DDL for `alumni_person` / `tg_identity` /
`alumni_program_years` is owned by the scraper; here we only read/write.
Callers wrap these in `session_scope()`, which commits — so these functions
do not commit themselves.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import func, Text

from model import AlumniPerson, TgIdentity

_TG_RE = re.compile(r"(?:t|telegram)\.me/([A-Za-z0-9_]+)")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now():
    return datetime.now(timezone.utc)


def normalize_username(u):
    if not u:
        return None
    u = u.strip()
    m = _TG_RE.search(u)
    if m:
        u = m.group(1)
    u = u.lstrip("@").strip().lower()
    return u or None


def normalize_email(e):
    if not e:
        return None
    return e.strip().lower() or None


def find_by_username(session, username):
    un = normalize_username(username)
    if not un:
        return None
    return (session.query(AlumniPerson)
            .filter(func.lower(AlumniPerson.telegram_username) == un,
                    AlumniPerson.removed_at.is_(None))
            .first())


def find_by_email(session, email):
    """Match by email. Portable across SQLite (test) and Postgres (prod):
    compares against the JSON text of the `emails` list (`["a@b.com"]`).

    Security: the value is embedded in a LIKE pattern, so reject non-emails and
    escape LIKE wildcards (`%`/`_`). Without this, a newcomer could enter `%` to
    match an arbitrary alumnus and bypass the whois gate / impersonate them."""
    em = normalize_email(email)
    if not em or not _EMAIL_RE.match(em):
        return None
    esc = em.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return (session.query(AlumniPerson)
            .filter(AlumniPerson.emails.cast(Text).ilike(f'%"{esc}"%', escape="\\"),
                    AlumniPerson.removed_at.is_(None))
            .first())


def max_grad_year(session):
    return session.query(func.max(AlumniPerson.grad_year_max)).scalar()


def classify(choice, year, max_year):
    """Category for a newcomer not auto-matched. choice: alumnus|student|friend|employee."""
    if choice == "student":
        return "student"
    if choice in ("friend", "employee"):
        return choice
    if year is not None and max_year is not None and year > max_year:
        return "student"
    return "unresolved_alumni"


def upsert_identity(session, user_id, **fields):
    """Create/update a tg_identity in the caller's session (no commit here)."""
    if "username" in fields:
        fields["username"] = normalize_username(fields["username"])
    if "declared_email" in fields:
        fields["declared_email"] = normalize_email(fields["declared_email"])
    ts = _now()
    ident = session.get(TgIdentity, user_id)
    if ident is None:
        ident = TgIdentity(user_id=user_id, first_seen=ts, last_seen=ts)
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
    return ident


def classes_str(alum):
    return ", ".join(alum.classes or [])


def format_welcome(template, alum):
    return (template
            .replace("%NAME%", alum.name or "")
            .replace("%FIRST_NAME%", alum.first_name or "")
            .replace("%LAST_NAME%", alum.last_name or "")
            .replace("%CLASS%", classes_str(alum))
            .replace("%PROGRAM%", ", ".join(alum.programs or [])))
