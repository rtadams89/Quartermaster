import json

import pytest

from app import lookup
from app.db import SessionLocal
from app.models import UpcLookup

CODE = "029465087603"
ITEM = {"code": "OK", "items": [{"title": "Federal American Eagle 9mm Luger 115 Grain FMJ - 50 Rounds",
                                 "brand": "Federal", "description": "Range ammo.",
                                 "images": ["http://insecure/x.jpg", "https://img.example/a.jpg"]}]}


@pytest.fixture()
def fake(monkeypatch):
    class Fake(list):
        reply = ITEM

    calls = Fake()

    def _fetch(code):
        calls.append(code)
        return calls.reply

    monkeypatch.setattr(lookup, "_fetch", _fetch)
    return calls


def test_lookup_suggests_details_and_caches(authed, fake):
    r = authed.get(f"/api/lookup/{CODE}").json()
    assert r["found"] and r["brand"] == "Federal" and r["image"] == "https://img.example/a.jpg"
    s = r["suggestion"]
    assert s["rounds_per_box"] == 50 and s["bullet_weight_gr"] == 115 and s["bullet_type"] == "FMJ"
    assert s["brand"] == "Federal" and s["name"].startswith("American Eagle")
    cal = {c["id"]: c["name"] for c in authed.get("/api/calibers").json()}
    assert "9" in cal[s["caliber_id"]]
    authed.get(f"/api/lookup/{CODE}")
    assert fake == [CODE]  # second ask came from the cache


def test_lookup_equivalent_ean13_form_is_one_code(authed, fake):
    authed.get(f"/api/lookup/{CODE}")
    authed.get(f"/api/lookup/0{CODE}")
    assert fake == [CODE]


def test_not_found_is_cached_but_errors_are_not(authed, fake, monkeypatch):
    fake.reply = {"code": "OK", "items": []}
    assert authed.get(f"/api/lookup/{CODE}").json()["found"] is False
    authed.get(f"/api/lookup/{CODE}")
    assert len(fake) == 1

    def boom(code):
        raise lookup.LookupFailed("down")

    monkeypatch.setattr(lookup, "_fetch", boom)
    r = authed.get("/api/lookup/036000291452")
    assert r.status_code == 503 and r.json()["detail"] == "down"
    with SessionLocal() as db:
        assert db.get(UpcLookup, "036000291452") is None


def test_own_labels_and_odd_codes_are_never_sent(authed, fake):
    for code in ("QM000001", "12345"):
        assert authed.get(f"/api/lookup/{code}").json()["found"] is False
    assert fake == []


def test_can_be_switched_off(authed, fake, monkeypatch):
    monkeypatch.setattr("app.config.UPC_LOOKUP", False)
    assert authed.get(f"/api/lookup/{CODE}").json() == {"enabled": False, "found": False}
    assert fake == []


def test_requires_login(client):
    assert client.get(f"/api/lookup/{CODE}").status_code == 401


def test_rate_limit_message(authed, monkeypatch):
    monkeypatch.setattr(lookup, "_fetch", lambda c: {"code": "EXCEED_LIMIT", "items": []})
    r = authed.get(f"/api/lookup/{CODE}")
    assert r.status_code == 503 and "limit" in r.json()["detail"]


@pytest.mark.parametrize("title,want", [
    ("Winchester USA .45 ACP 230gr FMJ 50ct", (50, 230.0, "FMJ")),
    ("Hornady 223 Rem 55 gr SP, Box of 20", (20, 55.0, "SP")),
    ("Mystery thing", (None, None, None)),
])
def test_guess_parsing(authed, title, want):
    with SessionLocal() as db:
        g = lookup.guess(db, UpcLookup(code="x", found=True, title=title, brand="", description=""))
    assert (g.get("rounds_per_box"), g.get("bullet_weight_gr"), g.get("bullet_type")) == want


def test_guess_matches_user_named_caliber(authed):
    ids = {c["name"]: c["id"] for c in authed.get("/api/calibers").json()}
    with SessionLocal() as db:
        def pick(title):
            return lookup.guess(db, UpcLookup(code="x", found=True, title=title, brand="", description="")).get("caliber_id")
        assert pick("Winchester 45 Auto 230 gr FMJ") == ids[".45 ACP"]
        assert pick("PMC Bronze 5.56x45mm NATO 55gr") == ids[".223 Rem / 5.56 NATO"]
        assert pick("Hornady 308 Winchester 168gr") == ids[".308 Win / 7.62x51"]
        assert pick("Some 7mm Rem Mag") is None
