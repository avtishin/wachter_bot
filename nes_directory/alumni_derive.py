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
