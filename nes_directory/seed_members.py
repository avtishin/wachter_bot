"""One-time seed of tg_identity from members.csv (current chat roster with
telegram user_ids). Rows matched by username against the directory become
`alumni` bindings; the rest are recorded as `unknown` (id+username kept for
future reconciliation).

  python3 seed_members.py [members.csv]
"""
import csv
import sys
import pathlib

from sqlalchemy.orm import sessionmaker

import alumni_models as m
import alumni_link as al


def seed(csv_path, engine=None):
    engine = engine or m.get_engine()
    m.init_db(engine)
    session = sessionmaker(bind=engine)()
    n_total = n_alumni = 0
    try:
        with open(csv_path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                raw_id = (row.get("user_id") or "").strip()
                if not raw_id:
                    continue
                user_id = int(raw_id)
                username = (row.get("username") or "").strip() or None
                alum = al.find_by_username(session, username) if username else None
                if alum:
                    al.upsert_identity(session, user_id, username=username,
                                       category="alumni", alumni_uid=alum.uid,
                                       source="members_csv")
                    n_alumni += 1
                else:
                    al.upsert_identity(session, user_id, username=username,
                                       category="unknown", source="members_csv")
                n_total += 1
    finally:
        session.close()
    print(f"seed_members: {n_total} identities, {n_alumni} matched as alumni")
    return {"total": n_total, "alumni": n_alumni}


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else str(
        pathlib.Path(__file__).parent / "members.csv")
    seed(path)
