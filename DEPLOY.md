# Deploy: bot + alumni dashboard on one Postgres (Railway)

Two services share **one** Postgres:

- **wachter bot** (`wachter/`, root `Dockerfile`) — owns `chats` / `users`
  schema via Alembic; reads `alumni_*` / `tg_identity`, writes `tg_identity`.
- **alumni scraper + dashboard** (`nes_directory/`, `nes_directory/Dockerfile`)
  — owns and creates the `alumni_*` / `tg_identity` / `alumni_program*` tables;
  serves the dashboard and runs the scraper on demand.

## 1. Provision Postgres

Create one Railway Postgres. Note its connection string as `DATABASE_URL`
(Railway gives `postgres://…`; both apps normalize it to `postgresql://…`).

## 2. Create the shared schema (once, ordered)

The scraper owns the `alumni_*` / `tg_identity` tables — create them first so
the bot can read them:

```bash
# from nes_directory/, with DATABASE_URL pointing at the Railway Postgres
DATABASE_URL=<railway> python -c "import alumni_models; alumni_models.init_db()"
```

Then load data (either migrate an existing local SQLite export or scrape fresh):

```bash
DATABASE_URL=<railway> python nes_db.py ingest          # needs out/alumni.json
DATABASE_URL=<railway> python seed_members.py members.csv   # optional: seed roster
```

## 3. Bot service (Railway)

- Root `Dockerfile` (`CMD alembic upgrade head && python wachter/bot.py`).
- The Alembic migration `e1f2a3b4c5d6` adds `on_alumni_welcome_message` /
  `on_email_prompt_message` to `chats` — `alembic upgrade head` applies it.
- Env: `DATABASE_URL` (shared), `TELEGRAM_TOKEN`.

## 4. Dashboard service (Railway)

- `nes_directory/Dockerfile` (gunicorn `app:app`).
- Env: `DATABASE_URL` (shared), `APP_PASSWORD` (+ `APP_USER`), and — for the
  scraper — `NES_LOGIN` / `NES_PASSWORD` (creds.env or env vars).
- No local `db` service on Railway: it uses the shared Postgres. The
  `nes_directory/docker-compose.yml` `db` service is for local dev only.

## Ownership summary

| Table(s) | Created by | Migrated by |
|----------|-----------|-------------|
| `chats`, `users` | bot | bot Alembic (`migrations/`) |
| `alumni_person` / `_history` / `_change_log` / `_crawl` / `_raw_card` / `_program` / `alumni_program_years` | scraper `init_db` (create_all) | scraper |
| `tg_identity` | scraper `init_db` | scraper |

The bot's read/write models for the shared tables map to the same names; the
bot never creates them (its Alembic migrations cover only `chats`/`users`).

## Ordering / gotchas

- Run step 2 before starting the bot, or the bot's alumni lookups hit a
  missing relation. `init_db` is idempotent (create-if-not-exists).
- Re-running the scraper (`ingest`) refreshes the directory and reconciles
  `tg_identity` automatically.
