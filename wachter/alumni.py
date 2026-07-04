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


def valid_email(e):
    """Normalized email if it looks like an address, else None."""
    em = normalize_email(e)
    return em if em and _EMAIL_RE.match(em) else None


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
    em = valid_email(email)
    if not em:
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


def bio(alum):
    """Short one-line bio from the directory card: latest job + residence."""
    full = getattr(alum, "full", None) or {}
    parts = []
    work = full.get("work") or []
    if work:
        w = work[0]
        job = " · ".join(x for x in (w.get("position"), w.get("company")) if x)
        if job:
            parts.append(job)
    if full.get("residence"):
        parts.append(f"📍 {full['residence']}")
    return " · ".join(parts)


def alumni_whois_message(template, alum):
    """Welcome + short bio + searchable #whois tag (name/class only, no email)."""
    msg = format_welcome(template, alum)
    b = bio(alum)
    if b:
        msg += f"\n{b}"
    classes = classes_str(alum)
    if classes:
        msg += f"\n\n#whois {classes}"
    return msg


def identity_greeting(ident, alum, template):
    """Recap for a returning known participant: alumnus card, or their declared
    name/program/intro — instead of a bare 'welcome again'."""
    if alum is not None:
        return alumni_whois_message(template, alum)
    prog, year = ident.declared_program, ident.declared_year
    tag = f"{prog}'{year}" if prog and year else (prog or "")
    body = ident.intro or ident.declared_name or ""
    msg = (f"С возвращением!\n{body}").rstrip() if body else "С возвращением!"
    if tag:
        msg += f"\n\n#whois {tag}"
    return msg
