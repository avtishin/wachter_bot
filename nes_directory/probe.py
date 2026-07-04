"""Login probe for my.nes.ru — authenticates and dumps the authenticated
directory page so we can learn its HTML structure. No Playwright needed:
the site is server-rendered (Windows-1251) with a plain POST login form.
"""
import os
import sys
import pathlib

import requests

BASE = "https://my.nes.ru"
ADAM = f"{BASE}/adam.pl"
HERE = pathlib.Path(__file__).parent
RAW = HERE / "raw_html"
RAW.mkdir(exist_ok=True)


def load_creds():
    login = os.environ.get("NES_LOGIN")
    password = os.environ.get("NES_PASSWORD")
    env_file = HERE / "creds.env"
    if (not login or not password) and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "NES_LOGIN" and not login:
                login = v
            elif k == "NES_PASSWORD" and not password:
                password = v
    if not login or not password:
        sys.exit("Нет кредов. Создай creds.env (см. creds.env.example) "
                 "или экспортируй NES_LOGIN / NES_PASSWORD.")
    return login, password


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
    })
    s.cookies.set("lang", "1", domain="my.nes.ru")
    return s


def login(s, user, pw):
    data = {
        "login": user,
        "password": pw,
        "persistent": "on",
        "go": "guest/login",
        "query": "student/directory/list_directory&program=1",
    }
    r = s.post(ADAM, data=data, timeout=30, allow_redirects=True)
    r.encoding = "cp1251"
    return r


def get(s, query):
    r = s.get(ADAM, params=query, timeout=30)
    r.encoding = "cp1251"
    return r


def looks_logged_in(html):
    low = html.lower()
    # login form present => NOT logged in
    if 'name="password"' in low and 'guest/login' in low:
        return False
    return True


def main():
    user, pw = load_creds()
    s = make_session()
    print(f"Логинимся как {user!r} ...")
    r = login(s, user, pw)
    print("  HTTP", r.status_code, "| url:", r.url)
    print("  cookies:", {c.name: c.value[:8] + "…" for c in s.cookies})

    # Fetch directory program=1 explicitly with the session
    r2 = get(s, "student/directory/list_directory&program=1")
    ok = looks_logged_in(r2.text)
    print("  logged_in:", ok)
    (RAW / "directory_program1.html").write_text(r2.text, encoding="utf-8")
    print("  saved raw_html/directory_program1.html", len(r2.text), "bytes")

    if not ok:
        # dump login response too for debugging
        (RAW / "login_response.html").write_text(r.text, encoding="utf-8")
        print("  !! Похоже, логин не прошёл. См. raw_html/login_response.html")
        sys.exit(1)


if __name__ == "__main__":
    main()
