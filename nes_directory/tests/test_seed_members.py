import alumni_models as m
import seed_members


def test_seed(pg_session, tmp_path):
    eng = pg_session.get_bind()
    pg_session.add(m.AlumniPerson(uid="10", name="Tishin A", telegram_username="very_big_t",
                                  emails=[], classes=["MAE'2019"], grad_year_max=2019))
    pg_session.commit()

    csv_path = tmp_path / "members.csv"
    csv_path.write_text(
        "user_id,username,first_name,last_name,unofficial_client\n"
        "95,very_big_t,Sasha,T,нет\n"
        "218,someoneelse,Max,P,нет\n"
        ",noid,No,Id,нет\n",   # blank user_id -> skipped
        encoding="utf-8")

    res = seed_members.seed(str(csv_path), engine=eng)
    assert res["total"] == 2 and res["alumni"] == 1

    matched = pg_session.get(m.TgIdentity, 95)
    assert matched.category == "alumni" and matched.alumni_uid == "10"
    unknown = pg_session.get(m.TgIdentity, 218)
    assert unknown.category == "unknown" and unknown.alumni_uid is None
