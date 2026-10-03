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


def test_guess_knows_the_standard_names_and_the_common_ones(authed):
    names = [".32 Auto", ".30-30 Winchester", ".460 S&W Magnum", ".50 Action Express", "9x18 Makarov",
             "7.62x25 Tokarev", ".45-70 Government", "7.5x55 Swiss"]
    ids = {}
    for n in names:
        r = authed.post("/api/calibers", json={"name": n})
        assert r.status_code == 200, r.text
        ids[n] = r.json()["id"]
    with SessionLocal() as db:
        def pick(title):
            return lookup.guess(db, UpcLookup(code="x", found=True, title=title, brand="", description="")).get("caliber_id")
        assert pick("PPU Handgun .32 ACP 71 gr FMJ 50 rounds") == ids[".32 Auto"]
        assert pick("Fiocchi 30-30 Win 170 gr SP") == ids[".30-30 Winchester"]
        assert pick("Hornady 460 S&W Magnum 200 gr FTX") == ids[".460 S&W Magnum"]
        assert pick("Armscor .50 AE 300 gr JHP") == ids[".50 Action Express"]
        assert pick("S&B 9mm Makarov 95 gr FMJ") == ids["9x18 Makarov"]
        assert pick("PPU 7.62x25mm Tokarev 85 gr") == ids["7.62x25 Tokarev"]
        assert pick("Remington 45-70 Govt 405 gr") == ids[".45-70 Government"]


# ------------------------------------------------------------- listing photo
def _png(size, noisy=True):
    import io

    from PIL import Image

    img = Image.effect_noise(size, 60) if noisy else Image.new("L", size, 200)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _scanned(c):
    """A code the kiosk has seen, plus a cached lookup that has a listing photo."""
    b = c.post("/api/batches", json={"kind": "in"}).json()
    c.post(f"/api/batches/{b['id']}/scan", json={"code": CODE})
    c.post(f"/api/batches/{b['id']}/finish")
    c.get(f"/api/lookup/{CODE}")


def test_listing_photo_is_kept_when_there_is_no_photo(authed, fake, monkeypatch):
    _scanned(authed)
    monkeypatch.setattr(lookup, "fetch_image", lambda url: _png((400, 300)))
    assert authed.post(f"/api/lookup/{CODE}/photo").json() == {"saved": True}
    assert authed.get(f"/api/barcodes/{CODE}/photo").status_code == 200


def test_your_own_photo_is_never_replaced(authed, fake, monkeypatch):
    _scanned(authed)
    mine = _png((300, 300))
    assert authed.put(f"/api/barcodes/{CODE}/photo", content=mine).status_code == 200
    before = authed.get(f"/api/barcodes/{CODE}/photo").content
    monkeypatch.setattr(lookup, "fetch_image", lambda url: pytest.fail("must not even download"))
    assert authed.post(f"/api/lookup/{CODE}/photo").json() == {"saved": False, "reason": "has_photo"}
    assert authed.get(f"/api/barcodes/{CODE}/photo").content == before


@pytest.mark.parametrize("data", [_png((100, 100)), _png((600, 100)), _png((400, 400), noisy=False), b"junk"])
def test_small_odd_shaped_blank_or_broken_images_are_not_kept(authed, fake, monkeypatch, data):
    _scanned(authed)
    monkeypatch.setattr(lookup, "fetch_image", lambda url: data)
    assert authed.post(f"/api/lookup/{CODE}/photo").json() == {"saved": False, "reason": "not_a_photo"}
    assert authed.get(f"/api/barcodes/{CODE}/photo").status_code == 404


def test_no_listing_image_or_failed_download_saves_nothing(authed, fake, monkeypatch):
    fake.reply = {"code": "OK", "items": [{"title": "Box", "brand": "", "images": []}]}
    _scanned(authed)
    assert authed.post(f"/api/lookup/{CODE}/photo").json() == {"saved": False, "reason": "no_image"}

    def boom(url):
        raise lookup.LookupFailed("down")

    fake.reply = ITEM
    with SessionLocal() as db:
        db.query(UpcLookup).delete()
        db.commit()
    authed.get(f"/api/lookup/{CODE}")
    monkeypatch.setattr(lookup, "fetch_image", boom)
    assert authed.post(f"/api/lookup/{CODE}/photo").json() == {"saved": False, "reason": "unavailable"}
    assert authed.get(f"/api/barcodes/{CODE}/photo").status_code == 404


def test_photo_endpoint_needs_known_code_and_login(authed, client):
    assert authed.post("/api/lookup/036000291452/photo").status_code == 404
    client.cookies.clear()
    assert client.post(f"/api/lookup/{CODE}/photo").status_code == 401


def test_downloads_only_from_public_https_hosts():
    for url in ("http://img.example/a.jpg", "https://127.0.0.1/a.jpg", "https://localhost/a.jpg",
                "https://169.254.169.254/latest", "https://[::1]/a.jpg", "file:///etc/passwd"):
        with pytest.raises(lookup.LookupFailed):
            lookup.fetch_image(url)
