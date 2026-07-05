import base64
import copy
import importlib

import pytest

REC = {
    "uid": "10", "name": "Tishin Aleksandr Vladimirovich",
    "listed_programs": ["Master of Arts in Economics [MAE]"],
    "listed_classes": ["MAE'2019"],
    "contact": {"links": [{"title": "Telegram", "url": "https://t.me/very_big_t"}]},
}


def _sess(engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine)()


@pytest.fixture()
def client(pg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("APP_PASSWORD", "secret")
    import alumni_models, alumni_link, nes_db, app as appmod
    importlib.reload(alumni_models)
    importlib.reload(alumni_link)
    importlib.reload(nes_db)
    importlib.reload(appmod)
    eng = alumni_models.get_engine(pg_url)
    alumni_models.init_db(eng)
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=eng)
    # an unresolved identity matching Tishin by username (added after ingest,
    # so auto-reconcile hasn't linked it — good for testing manual resolve)
    alumni_link.upsert_identity(_sess(eng), 95, username="very_big_t",
                                category="unknown", source="members_csv")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}


def test_identities_list(client):
    r = client.get("/identities", headers=_auth())
    assert r.status_code == 200 and b"very_big_t" in r.data


def test_identity_detail(client):
    r = client.get("/identities/95", headers=_auth())
    assert r.status_code == 200


def test_identity_resolve_links_alumnus(client):
    r = client.post("/identities/95/resolve", data={"alumni_uid": "10"}, headers=_auth())
    assert r.status_code in (302, 200)
    import alumni_models
    with alumni_models.session_scope() as s:
        ident = s.get(alumni_models.TgIdentity, 95)
        assert ident.category == "alumni" and ident.alumni_uid == "10"


def test_identity_category_change_unlinks(client):
    client.post("/identities/95/resolve", data={"alumni_uid": "10"}, headers=_auth())
    client.post("/identities/95/category", data={"category": "friend"}, headers=_auth())
    import alumni_models
    with alumni_models.session_scope() as s:
        ident = s.get(alumni_models.TgIdentity, 95)
        assert ident.category == "friend" and ident.alumni_uid is None


def test_identity_edit_fields(client):
    r = client.post("/identities/95/edit", headers=_auth(), data={
        "declared_name": "Иванов Иван", "intro": "живу в Москве",
        "declared_program": "MAE", "declared_year": "2019", "declared_email": ""})
    assert r.status_code in (302, 200)
    import alumni_models
    with alumni_models.session_scope() as s:
        ident = s.get(alumni_models.TgIdentity, 95)
        # имя и «о себе» разведены; год распарсен; пустой email -> None
        assert ident.declared_name == "Иванов Иван" and ident.intro == "живу в Москве"
        assert ident.declared_year == 2019 and ident.declared_email is None
