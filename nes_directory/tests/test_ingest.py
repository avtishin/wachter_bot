import copy
import nes_db
import alumni_models as m

REC = {
    "uid": "10", "name": "Tishin Aleksandr Vladimirovich", "sex": "Male",
    "birthday": "24 January", "residence": "Россия, г Москва",
    "listed_programs": ["Master of Arts in Economics [MAE]"],
    "listed_classes": ["MAE'2019"],
    "contact": {"links": [{"title": "Telegram", "url": "https://t.me/Very_Big_T"}],
                "emails": ["A.Tishin@nes.ru"]},
    "work": [{"company": "X", "position": "Lead"}],
}


def _engine(pg_session):
    return pg_session.get_bind()


def test_ingest_new_person(pg_session):
    eng = _engine(pg_session)
    res = nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={"10": "<html>x</html>"}, engine=eng)
    assert res["new"] == 1 and res["changed"] == 0
    p = pg_session.query(m.AlumniPerson).filter_by(uid="10").one()
    assert p.telegram_username == "very_big_t"
    assert p.emails == ["a.tishin@nes.ru"]
    assert p.first_name == "Aleksandr" and p.last_name == "Tishin"
    assert p.grad_year_max == 2019
    assert pg_session.query(m.AlumniProgramYear).filter_by(program_code="MAE", year=2019).count() == 1
    assert pg_session.query(m.AlumniRawCard).filter_by(uid="10").one().html == "<html>x</html>"
    assert pg_session.query(m.AlumniChangeLog).filter_by(uid="10", change_type="created").count() == 1


def test_ingest_detects_change(pg_session):
    eng = _engine(pg_session)
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=eng)
    rec2 = copy.deepcopy(REC)
    rec2["work"] = [{"company": "Y", "position": "CEO"}]
    res = nes_db.ingest("full", records=[rec2], cards={}, engine=eng)
    assert res["changed"] == 1
    assert pg_session.query(m.AlumniPersonHistory).filter_by(uid="10").count() == 2
    # field-level diff rows are written for the change (a list change to `work`
    # yields added+removed rows, not "changed")
    assert pg_session.query(m.AlumniChangeLog).filter(
        m.AlumniChangeLog.uid == "10",
        m.AlumniChangeLog.change_type.in_(["added", "removed", "changed"])).count() >= 1
    # derived fields survive an update (guards the emails-on-update path)
    p = pg_session.query(m.AlumniPerson).filter_by(uid="10").one()
    assert p.emails == ["a.tishin@nes.ru"]
    assert p.telegram_username == "very_big_t"


def test_ingest_full_marks_removed(pg_session):
    eng = _engine(pg_session)
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=eng)
    res = nes_db.ingest("full", records=[], cards={}, engine=eng)
    assert res["removed"] == 1
    assert pg_session.query(m.AlumniPerson).filter_by(uid="10").one().removed_at is not None


def test_ingest_new_kind_does_not_remove(pg_session):
    eng = _engine(pg_session)
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=eng)
    res = nes_db.ingest("new", records=[], cards={}, engine=eng)
    assert res["removed"] == 0
