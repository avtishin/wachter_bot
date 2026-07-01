# One-time: load scraped data into Postgres

Prereqs: `out/alumni.json` and `raw_html/cards/` already produced by the scraper,
and a reachable Postgres in `DATABASE_URL`.

    DATABASE_URL=postgresql://wachter:wachter@localhost:5432/wachter \
      ./.venv/bin/python -c "import nes_db; nes_db.ingest('full')"

Verify:

    DATABASE_URL=... ./.venv/bin/python -c "import nes_db; print(nes_db.stats())"
