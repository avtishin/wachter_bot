"""One-shot bootstrap of a target Postgres (e.g. Railway) from the two locally
synced sources: the scraped directory and the chat roster.

Creates the alumni_* / tg_identity schema, ingests the directory
(out/alumni.json + raw_html/cards/), seeds tg_identity from members.csv, and
reconciles. Run from `nes_directory/` with DATABASE_URL pointing at the target:

    DATABASE_URL=<target-postgres> python bootstrap.py

Idempotent: re-running refreshes the directory and re-seeds/reconciles without
duplicating rows.
"""
import pathlib

import alumni_models
import nes_db
import seed_members
import alumni_link

HERE = pathlib.Path(__file__).parent


def main():
    engine = alumni_models.get_engine()
    print("→ схема (init_db)")
    alumni_models.init_db(engine)

    print("→ ингест директории (out/alumni.json)")
    print("  ", nes_db.ingest("full", engine=engine))

    members = HERE / "members.csv"
    if members.exists():
        print("→ сид members.csv")
        seed_members.seed(str(members), engine=engine)
    else:
        print("  members.csv не найден — пропускаю сид ростера")

    print("→ реконсиляция")
    print("  ", alumni_link.reconcile_with_engine(engine))
    print("bootstrap: готово")


if __name__ == "__main__":
    main()
