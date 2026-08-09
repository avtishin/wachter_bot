"""my.nes.ru alumni directory scraper.

No Playwright: the portal is server-rendered (Windows-1251) with a plain POST
login. Three resumable phases:

  index     -> for every program fetch the class=all listing, collect (uid,
               name, class) rows; dedupe people by uid.       (out/index.json)
  download  -> fetch each person's card once, save raw HTML.  (raw_html/cards/)
  parse     -> turn every saved card into a rich JSON record. (out/alumni.json)

Run `python3 nes_scraper.py all` to do everything (download is resumable, so
re-running continues where it stopped). Be gentle: single-threaded, random
sleep between requests.

Credentials: creds.env (NES_LOGIN / NES_PASSWORD) — see probe.py.
"""
import os
import re
import sys
import json
import time
import random
import pathlib
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# --- reuse session/login helpers from probe.py -----------------------------
from probe import make_session, login, load_creds, ADAM

HERE = pathlib.Path(__file__).parent
RAW = HERE / "raw_html"
CARDS = RAW / "cards"
OUT = HERE / "out"
CARDS.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

# polite random delay between requests (seconds); overridable via env
MIN_DELAY = float(os.environ.get("NES_MIN_DELAY", "1.0"))
MAX_DELAY = float(os.environ.get("NES_MAX_DELAY", "2.5"))


def nap():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


_CHARSET_RE = re.compile(r"charset=windows-1251", re.I)


def to_utf8_html(text):
    """We save pages already decoded to UTF-8, but the markup still declares
    charset=Windows-1251 — which makes browsers render the raw file as
    mojibake. Rewrite the declaration so raw files open correctly. Parsing is
    unaffected (we always read these files as UTF-8)."""
    return _CHARSET_RE.sub("charset=utf-8", text)


def get(s, query, tries=4):
    """GET adam.pl?<query> as cp1251 text, with simple retry/backoff."""
    last = None
    for attempt in range(tries):
        try:
            r = s.get(ADAM, params=query, timeout=40)
            r.encoding = "cp1251"
            if r.status_code == 200 and len(r.text) > 200:
                return r.text
            last = f"HTTP {r.status_code}, {len(r.text)} bytes"
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(2 * (attempt + 1) + random.random())
    raise RuntimeError(f"GET failed for {query!r}: {last}")


def fmt_eta(done, total, started):
    if done == 0:
        return "?"
    elapsed = time.time() - started
    rate = elapsed / done
    remain = rate * (total - done)
    return f"{remain/60:.1f} min (elapsed {elapsed/60:.1f} min)"


# --- phase 1: index --------------------------------------------------------
def discover_programs(s):
    """Parse the alumni_program <select> -> {id: full title}."""
    html = get(s, "student/directory/list_directory&program=1")
    soup = BeautifulSoup(html, "html.parser")
    sel = soup.find("select", attrs={"name": "alumni_program"})
    progs = {}
    for opt in sel.find_all("option"):
        progs[opt["value"]] = opt.get_text(strip=True)
    return progs


ROW_RE = re.compile(
    r'go_info\((\d+)\);"><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td>', re.S)


def build_index(s):
    programs = discover_programs(s)
    print(f"Программ найдено: {len(programs)}")
    people = {}          # uid -> {name, classes:set, programs:set}
    for pid, title in programs.items():
        html = get(s, f"student/directory/list_directory&alumni_program={pid}&class=all")
        rows = ROW_RE.findall(html)
        for uid, _num, name_html, klass in rows:
            name = BeautifulSoup(name_html, "html.parser").get_text(" ", strip=True)
            klass = klass.strip()
            rec = people.setdefault(uid, {"uid": uid, "name": name,
                                          "classes": set(), "programs": set()})
            if klass:
                rec["classes"].add(klass)
            rec["programs"].add(title)
        print(f"  program {pid:>2} {title[:45]:<45} rows={len(rows):>4} "
              f"unique-so-far={len(people)}")
        nap()
    # serialise sets
    index = {uid: {"uid": uid, "name": r["name"],
                   "classes": sorted(r["classes"]),
                   "programs": sorted(r["programs"])}
             for uid, r in people.items()}
    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nИндекс: {len(index)} уникальных людей -> out/index.json")
    return index


def load_index():
    p = OUT / "index.json"
    if not p.exists():
        sys.exit("Нет out/index.json — сначала запусти фазу index.")
    return json.loads(p.read_text(encoding="utf-8"))


# --- phase 2: download cards ----------------------------------------------
def download_cards(s, index):
    # NES_REFRESH=1 -> re-fetch every card (Tier B full recrawl to catch
    # profile edits). Default: skip cards already on disk (resume / new only).
    refresh = os.environ.get("NES_REFRESH") == "1"
    uids = list(index.keys())
    total = len(uids)
    started = time.time()
    done = skipped = 0
    for i, uid in enumerate(uids, 1):
        dest = CARDS / f"{uid}.html"
        if not refresh and dest.exists() and dest.stat().st_size > 500:
            skipped += 1
            continue
        html = get(s, f"student/directory/view_person&uid={uid}")
        dest.write_text(to_utf8_html(html), encoding="utf-8")
        done += 1
        if done % 10 == 0 or i == total:
            pct = 100 * i / total
            print(f"[download] {i}/{total} ({pct:4.1f}%) "
                  f"new={done} skipped={skipped} "
                  f"last={index[uid]['name'][:30]!r:32} ETA {fmt_eta(done, total-skipped, started)}",
                  flush=True)
        nap()
    print(f"[download] готово: скачано {done}, было {skipped}, всего {total}")


# --- phase 3: parse cards --------------------------------------------------
def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def _section_tables(soup):
    """Yield (section_id, title, tbody) for each card section."""
    for tbody in soup.find_all("tbody", attrs={"name": "tbodyy"}):
        sid = (tbody.get("id") or "").replace("thead_", "") or "gen"
        # title from the subheader cell in the parent table
        title = ""
        table = tbody.find_parent("table")
        if table:
            sub = table.find("th", class_="subheader")
            if sub:
                title = _txt(sub)
        yield sid, title, tbody


def _rows(tbody):
    """Return list of (label, value_td) for th/td rows."""
    out = []
    for tr in tbody.find_all("tr", recursive=False):
        th = tr.find("th", recursive=False)
        tds = tr.find_all("td", recursive=False)
        if th is None:
            continue
        label = _txt(th).rstrip(":").strip()
        # value = first td that is not the photo cell
        val_td = None
        for td in tds:
            if td.find("img") and "view_photo" in str(td):
                continue
            val_td = td
            break
        out.append((label, val_td))
    return out


UPDATED_RE = re.compile(r"\(updated\s+([0-9.]+)\)")


def parse_contact(tbody):
    out = {}
    for label, td in _rows(tbody):
        if td is None:
            continue
        low = label.lower()
        if "mail" in low:
            emails = []
            for a in td.find_all("a", href=re.compile(r"^mailto:", re.I)):
                emails.append(a.get_text(strip=True))
            out["emails"] = emails
            out["emails_raw"] = _txt(td)
        elif "phone" in low:
            raw = _txt(td)
            # phones are plain text chunks before each "(updated …)"
            phones = re.findall(r"([+]?[\d][\d ()\-]{5,}\d)", raw)
            out["phones"] = [p.strip() for p in phones]
            out["phones_raw"] = raw
        elif "link" in low:
            links = []
            for a in td.find_all("a", href=True):
                links.append({"title": a.get("title") or _txt(a) or a["href"],
                              "url": a["href"]})
            out["links"] = links
        else:
            out[label] = _txt(td)
    return out


def parse_education(tbody):
    out = []
    for label, td in _rows(tbody):
        out.append({"program": label, "status": _txt(td)})
    return out


def parse_work(tbody):
    jobs = []
    cur = None
    for tr in tbody.find_all("tr", recursive=False):
        th = tr.find("th", recursive=False)
        if th is None:
            # spacer row -> end of current job block
            if cur:
                jobs.append(cur)
                cur = None
            continue
        label = _txt(th).rstrip(":").strip()
        td = tr.find("td", recursive=False)
        val = _txt(td)
        if label.lower().startswith("company"):
            if cur:
                jobs.append(cur)
            cur = {"company": val}
            a = td.find("a", href=True) if td else None
            if a:
                m = re.search(r"company_id=(\d+)", a["href"])
                if m:
                    cur["company_id"] = m.group(1)
        elif cur is not None:
            cur[label.lower().replace(" ", "_")] = val
        else:
            cur = {label.lower().replace(" ", "_"): val}
    if cur:
        jobs.append(cur)
    return jobs


def parse_blocks(tbody, start_prefix):
    """Generic repeated-block parser (like Work): a new block starts on a row
    whose label begins with `start_prefix`; spacer rows end a block."""
    blocks = []
    cur = None
    for tr in tbody.find_all("tr", recursive=False):
        th = tr.find("th", recursive=False)
        if th is None:
            if cur:
                blocks.append(cur)
                cur = None
            continue
        label = _txt(th).rstrip(":").strip()
        td = tr.find("td", recursive=False)
        key = label.lower().replace(" ", "_").replace("/", "_")
        val = _txt(td)
        if label.lower().startswith(start_prefix):
            if cur:
                blocks.append(cur)
            cur = {key: val}
        elif cur is not None:
            cur[key] = val
        else:
            cur = {key: val}
    if cur:
        blocks.append(cur)
    return blocks


def parse_expertise(tbody):
    groups = []
    for tr in tbody.find_all("tr", recursive=False):
        td = tr.find("td")
        if not td:
            continue
        b = td.find("b")
        group = _txt(b) if b else ""
        items = []
        for el in td.find_all(["nobr", "li"]):
            t = el.get_text(" ", strip=True)
            if t:
                items.append({"text": t, "kind": el.name})
        groups.append({"group": group, "items": items,
                       "raw": _txt(td)})
    return groups


def parse_card(html, uid):
    soup = BeautifulSoup(html, "html.parser")
    rec = {"uid": uid,
           "url": f"https://my.nes.ru/adam.pl?student/directory/view_person&uid={uid}",
           "photo_url": f"https://my.nes.ru/adam.pl?student/people/view_photo&uid={uid}&size=2"}
    raw_sections = {}

    for sid, title, tbody in _section_tables(soup):
        # lossless raw capture for every section
        raw_rows = []
        for label, td in _rows(tbody):
            raw_rows.append([label, _txt(td)])
        raw_sections[sid] = {"title": title, "rows": raw_rows,
                             "text": tbody.get_text("\n", strip=True)}

        if sid == "gen":
            for label, td in _rows(tbody):
                key = {"Name": "name", "Sex": "sex", "Birthday": "birthday",
                       "Place of residence": "residence"}.get(label, label)
                rec[key] = _txt(td)
        elif sid == "con":
            rec["contact"] = parse_contact(tbody)
        elif sid == "stud":
            rec["education"] = parse_education(tbody)
        elif sid == "job":
            rec["work"] = parse_work(tbody)
        elif sid == "fac":
            rec["teaching"] = [{"label": l, "value": _txt(td)} for l, td in _rows(tbody)]
        elif sid == "hobbies":
            rec["interests"] = " ".join(_txt(td) for _l, td in _rows(tbody) if td)
        elif sid == "expertise":
            rec["expertise"] = parse_expertise(tbody)
        elif sid == "edu":
            rec["education_after_nes"] = parse_blocks(tbody, "university")
        elif sid == "leader":
            txt = tbody.get_text(" ", strip=True)
            rec["class_leader"] = re.findall(r"[A-Za-z]+'?\d{4}", txt) or \
                ([txt] if txt else [])
        else:
            rec.setdefault("other_sections", {})[sid] = {
                "title": title,
                "rows": [[l, _txt(td)] for l, td in _rows(tbody)]}

    rec["_raw_sections"] = raw_sections
    return rec


def parse_all(index):
    records = []
    files = sorted(CARDS.glob("*.html"), key=lambda p: int(p.stem))
    total = len(files)
    for i, f in enumerate(files, 1):
        uid = f.stem
        html = f.read_text(encoding="utf-8")
        try:
            rec = parse_card(html, uid)
        except Exception as e:  # never lose a card to a parse bug
            rec = {"uid": uid, "_parse_error": repr(e)}
        # enrich with index data (listing name / programs / classes)
        if uid in index:
            rec["listed_name"] = index[uid]["name"]
            rec["listed_programs"] = index[uid]["programs"]
            rec["listed_classes"] = index[uid]["classes"]
        records.append(rec)
        if i % 200 == 0 or i == total:
            print(f"[parse] {i}/{total}", flush=True)

    records.sort(key=lambda r: r.get("name") or r.get("listed_name") or "")
    (OUT / "alumni.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "alumni.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    # a slim version without the raw fallback, for convenience
    slim = []
    for r in records:
        s = {k: v for k, v in r.items() if not k.startswith("_")}
        slim.append(s)
    (OUT / "alumni_slim.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[parse] готово: {len(records)} карточек -> out/alumni.json "
          f"(+ .jsonl, + alumni_slim.json без сырых секций)")


# --- main ------------------------------------------------------------------
def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"== {datetime.now():%H:%M:%S} phase={phase} "
          f"delay={MIN_DELAY}-{MAX_DELAY}s ==")

    if phase == "parse":
        # parsing works fully offline from saved cards
        parse_all(load_index())
        return

    s = make_session()
    u, p = load_creds()
    login(s, u, p)
    print("Залогинены.")

    if phase in ("index", "all"):
        index = build_index(s)
    else:
        index = load_index()

    if phase in ("download", "all"):
        download_cards(s, index)

    if phase in ("parse", "all"):
        parse_all(index)


if __name__ == "__main__":
    main()
