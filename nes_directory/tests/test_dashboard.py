import base64
import copy
import os
import pytest


REC = {
    "uid": "10", "name": "Tishin Aleksandr Vladimirovich",
    "listed_programs": ["Master of Arts in Economics [MAE]"],
    "listed_classes": ["MAE'2019"],
    "contact": {"links": [{"title": "Telegram", "url": "https://t.me/very_big_t"}]},
    "work": [{"company": "X", "position": "Lead"}],
}


@pytest.fixture()
def client(pg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("APP_PASSWORD", "secret")
    import importlib, alumni_models, nes_db, app as appmod
    importlib.reload(alumni_models)
    importlib.reload(nes_db)
    importlib.reload(appmod)
    alumni_models.init_db(alumni_models.get_engine(pg_url))
    nes_db.ingest("full", records=[copy.deepcopy(REC)], cards={}, engine=alumni_models.get_engine(pg_url))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}


def test_alumni_search(client):
    r = client.get("/alumni?q=Tishin", headers=_auth())
    assert r.status_code == 200 and b"Tishin" in r.data


def test_alumni_detail(client):
    r = client.get("/alumni/10", headers=_auth())
    assert r.status_code == 200 and "Lead".encode() in r.data
