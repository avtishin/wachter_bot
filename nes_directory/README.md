# NES Alumni Directory

Сбор и веб-просмотр директории выпускников my.nes.ru: скрапер → SQLite с
историей изменений → Flask-дашборд (статистика, синхронизация в один клик,
просмотр карточек, поиск). Без Playwright — сайт server-rendered, логин это
обычный POST-формы, всё на `requests` + `bs4`.

---

## Быстрый старт (Docker)

Нужны: Docker + Docker Compose, файл `creds.env` с доступом к my.nes.ru.

```bash
cd nes_directory

# 1. доступ к my.nes.ru (если ещё нет)
cat > creds.env <<'EOF'
NES_LOGIN=твой_логин
NES_PASSWORD=твой_пароль
EOF

# 2. пароль для входа в дашборд
cp .env.example .env        # и впиши APP_PASSWORD внутри

# 3. поднять
docker compose up -d --build
```

Открой **http://localhost:8000** → логин `admin` (из `.env`), пароль — твой `APP_PASSWORD`.

Полезное:

```bash
docker compose logs -f      # логи
docker compose down         # остановить
docker compose up -d --build  # пересобрать после изменений кода
```

Данные (`out/` — SQLite/JSON/логи, `raw_html/` — сырые карточки) лежат на
хосте и монтируются в контейнер — переживают пересборку. В репозитории уже
собрана база на 2905 выпускников, так что дашборд сразу с данными.

---

## Что в интерфейсе

| Раздел | Что делает |
|--------|-----------|
| **Дашборд** (`/`) | статистика, кнопки синка + live-лог, настройка cron |
| **Выпускники** (`/alumni`) | поиск по имени, фильтр по программе, карточки |
| Карточка (`/alumni/<uid>`) | контакты, вся работа, образование, экспертиза, история правок |
| **Изменения** (`/changes`) | лог: кто что и когда поменял в профиле |

Две кнопки синхронизации:

- **➕ Добавить новых** — быстро (~1 мин): обновляет реестр и докачивает только
  новых выпускников. Гоняй хоть каждую неделю.
- **🔄 Полный пересинк** — долго (~2 ч): перекачивает все карточки и ловит
  правки в профилях. Раз в квартал.

**Cron** (на дашборде) по умолчанию **выключен** — синк только вручную. Можно
включить и задать расписание (например `0 4 * * 1` — пн 04:00).

Картинки не грузятся: вместо фото — аватар с инициалом, интерфейс работает
только с локальной БД.

---

## Запуск без Docker (для разработки)

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
APP_PASSWORD=secret ./.venv/bin/python app.py    # http://localhost:8000
```

---

## Как это устроено

| Файл | Назначение |
|------|-----------|
| `app.py` | Flask-дашборд (Basic Auth, триггеры, просмотр карточек, cron) |
| `runner.py` | оркестрация синка `index→download→parse→ingest` (лок + статус) |
| `nes_scraper.py` | скрапер: `index` / `download` / `parse` |
| `nes_db.py` | загрузка в SQLite с историей версий и changelog |
| `probe.py` | логин-хелперы (используются всеми) |
| `templates/` | HTML-шаблоны дашборда |
| `out/nes.db` | SQLite: `person`, `person_history`, `change_log`, `crawl` |
| `out/alumni.json` | полный JSON-экспорт (+ `alumni_slim.json`, `alumni.jsonl`) |
| `raw_html/cards/` | сырые карточки — перепарсинг без обращений к серверу |

**База данных.** `person` — текущее состояние, `person_history` — все версии
профиля (append-only), `change_log` — пофилдовые изменения (`created` / `added`
/ `removed` / `changed`), `crawl` — журнал запусков. Изменения детектятся по
sha256 канонизированной записи.

---

## CLI (то же самое из терминала)

```bash
# первичный сбор с нуля
python3 nes_scraper.py all        # index + download (~2ч) + parse
python3 nes_db.py ingest --kind full

# инкрементально
python3 nes_scraper.py index && python3 nes_scraper.py download \
  && python3 nes_scraper.py parse && python3 nes_db.py ingest --kind new

# полный пересинк (ловит правки)
NES_REFRESH=1 python3 nes_scraper.py download \
  && python3 nes_scraper.py parse && python3 nes_db.py ingest --kind full

# просмотр
python3 nes_db.py stats
python3 nes_db.py changes 50
```

Вежливость к серверу: один поток, случайная пауза `NES_MIN_DELAY`–`NES_MAX_DELAY`
(по умолчанию 1.0–2.5 с). Скачивание резюмируемое — прерванный `download`
докачивает недостающее.

> На сайте нет эндпоинта «recently updated», поэтому отлов правок профилей =
> полный пересинк всех карточек.

---

## Безопасность и приватность

- Директория содержит ПД выпускников (email, телефоны) — доступ только под своей
  учёткой выпускника. `creds.env`, `.env`, `out/`, `raw_html/` в `.gitignore`.
- Дашборд под HTTP Basic Auth; `APP_PASSWORD` обязателен (если не задан —
  генерируется случайный и пишется в лог). Ставь за reverse-proxy с HTTPS.
- Ссылки в карточках рендерятся только для схем `http/https/mailto`.
