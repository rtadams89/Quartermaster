import sqlite3
import time
from pathlib import Path

import pytest

from app import config, pricing
from tests.conftest import make_product, run_batch


def listing(descr, mfg, grains, price, count, condition="new", shipping="55", limit="-"):
    return {"retailer": "R", "descr": descr, "mfg": mfg, "caliber": "9mm Luger", "price": price, "count": count,
            "cp_cleaned": 0, "grains": grains, "condition": condition, "shipping_rating": shipping, "purchaselimit": limit}


FEDERAL = {"brand": "Federal", "name": "American Eagle", "grain": 115, "bullet_type": "FMJ"}


def test_price_per_round_uses_price_over_count_then_cents():
    assert pricing.per_round({"price": "$3.20", "count": 50}) == pytest.approx(0.064)
    assert pricing.per_round({"price": "$1,299.00", "count": "1000"}) == pytest.approx(1.299)
    assert pricing.per_round({"price": "", "count": 0, "cp_cleaned": 6.4}) == pytest.approx(0.064)
    assert pricing.per_round({}) is None


def test_estimate_averages_matching_new_listings_with_good_shipping_and_no_limit():
    items = [
        listing("Federal American Eagle 9mm 115 Grain Full Metal Jacket", "Federal", 115, "$20.00", 50),
        listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$21.00", 50),
        listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$22.00", 50),
        listing("Federal American Eagle 9mm 124gr FMJ", "Federal", 124, "$1.00", 50),
        listing("Winchester White Box 9mm 115gr FMJ", "Winchester", 115, "$1.00", 50),
        listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$1.00", 50, "remanufactured"),
        listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$1.00", 50, shipping="49"),
        listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$1.00", 50, limit=10),
        listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$1.00", 50, shipping=""),
        listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$24.00", 50, shipping="free"),
    ]
    r = pricing.estimate(FEDERAL, items)
    assert r["price"] == pytest.approx(0.435) and r["listings"] == 4 and r["confidence"] == "high"
    assert pricing.estimate(FEDERAL, [listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$20.00", 50, shipping="49")])["listings"] == 0


def test_shipping_and_purchase_limit_rules():
    assert pricing.good_shipping({"shipping_rating": "50"}) and pricing.good_shipping({"shipping_rating": "free"})
    assert pricing.good_shipping({"shipping_rating": "Free"}) and pricing.good_shipping({"shipping_rating": 62})
    assert not pricing.good_shipping({"shipping_rating": "49"}) and not pricing.good_shipping({"shipping_rating": "2"})
    assert not pricing.good_shipping({}) and not pricing.good_shipping({"shipping_rating": "n/a"})
    assert not pricing.has_purchase_limit({"purchaselimit": "-"}) and not pricing.has_purchase_limit({})
    assert pricing.has_purchase_limit({"purchaselimit": 10}) and pricing.has_purchase_limit({"purchaselimit": "5"})


def test_estimate_falls_back_to_looser_matches_and_flags_them():
    items = [listing("Federal Champion Training 9mm 115gr FMJ", "Federal", 115, "$25.00", 50),
             listing("Federal Champion Training 9mm 115gr FMJ", "Federal", 115, "$26.00", 50)]
    r = pricing.estimate(FEDERAL, items)
    assert r["confidence"] == "low" and r["listings"] == 2 and r["price"] == pytest.approx(0.51)
    assert pricing.estimate(FEDERAL, [])["confidence"] == "none"


def test_search_words_skip_reason_and_caliber_guess():
    assert pricing.search_words({"brand": "Sellier & Bellot", "grain": 260.0}) == (260, "Sellier")
    assert pricing.search_words({"brand": "Misc", "grain": 0}) == (None, "Misc")
    assert pricing.skip_reason({"brand": "Misc"}) and pricing.skip_reason({"brand": ""}) and not pricing.skip_reason({"brand": "Federal"})
    options = [{"label": "9mm Luger", "slug": "9mm-luger"}, {"label": ".45 ACP", "slug": "45acp"}]
    assert pricing.guess_slug(["", "9mm Luger Subsonic"], options) == "9mm-luger"
    assert pricing.guess_slug(["26.5mm"], options) is None


@pytest.fixture()
def fake_service(monkeypatch):
    calls = []

    def fetch(endpoint, params, key):
        calls.append((endpoint, dict(params), key))
        if endpoint == "get_ammo_caliber_list":
            return {"data": [{"label": "9mm Luger", "seo": "9mm-luger"}, {"label": "10mm Auto", "seo": "10mm-auto"}]}
        assert key == "secret-key"
        return {"status": "success", "data": {"total": 3, "filtered": 3, "items": [
            listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$20.00", 50),
            listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$21.00", 50),
            listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$22.00", 50)]}}

    monkeypatch.setattr(pricing, "fetch", fetch)
    monkeypatch.setattr(pricing, "_pause", lambda s: None)
    return calls


def wait_done(c, job):
    for _ in range(100):
        j = c.get(f"/api/prices/jobs/{job['id']}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("price job did not finish")


def test_price_check_review_and_apply(authed, fake_service):
    c = authed
    cal = c.get("/api/calibers").json()[0]
    p = make_product(c, cost_per_box=5.0)
    other = make_product(c, brand="Misc", name="Loose", bullet_weight_gr=0, rounds_per_box=1)

    assert c.post("/api/prices/jobs", json={"scope": "product", "id": p["id"]}).status_code == 400
    assert c.put("/api/prices/settings", json={"key": "secret-key", "per_minute": 250}).json()["key_set"] is True
    assert "secret-key" not in str(c.get("/api/prices/config").json())

    opts = c.post("/api/prices/options").json()["options"]
    assert [o["slug"] for o in opts] == ["10mm-auto", "9mm-luger"]
    c.put(f"/api/prices/calibers/{cal['id']}", json={"slug": "9mm-luger"})

    plan = c.post("/api/prices/plan", json={"scope": "caliber", "id": cal["id"], "in_stock_only": False}).json()
    assert plan["products"] == 2 and plan["calls"] == 1 and plan["unmatched"] == [] and plan["options_loaded"]

    job = c.post("/api/prices/jobs", json={"scope": "caliber", "id": cal["id"], "in_stock_only": False}).json()
    done = wait_done(c, job)
    assert done["state"] == "done" and done["calls"] == 1
    by_id = {r["id"]: r for r in done["results"]}
    assert by_id[p["id"]]["price"] == pytest.approx(0.42) and by_id[p["id"]]["confidence"] == "high"
    assert by_id[p["id"]]["current"] == pytest.approx(0.1)
    assert by_id[other["id"]]["price"] is None and "surplus" in by_id[other["id"]]["note"]
    search = [x for x in fake_service if x[0] == "search_ammo"][0]
    assert search[1]["caliber"] == "9mm-luger" and search[1]["grains"] == 115 and search[1]["include"] == "Federal"
    assert search[1]["length"] == 40 and search[1]["start"] == 0

    assert c.post("/api/prices/apply", json={"items": [{"product_id": p["id"], "price": 0.42}]}).json() == {"saved": 1}
    after = [x for x in c.get("/api/products").json() if x["id"] == p["id"]][0]
    assert after["cost_per_box"] == pytest.approx(21.0) and after["cost_per_round"] == pytest.approx(0.42) and after["price_updated"]


def test_only_products_in_stock_are_checked_for_all(authed, fake_service):
    c = authed
    c.put("/api/prices/settings", json={"key": "secret-key", "per_minute": 250})
    cal = c.get("/api/calibers").json()[0]
    c.put(f"/api/prices/calibers/{cal['id']}", json={"slug": "9mm-luger"})
    have = make_product(c, name="Have")
    make_product(c, name="None")
    assert c.post(f"/api/products/{have['id']}/barcodes", json={"code": "012345678905"}).status_code == 200
    run_batch(c, "in", [("012345678905", 2)])
    plan = c.post("/api/prices/plan", json={"scope": "all", "in_stock_only": True}).json()
    assert plan["products"] == 1
    plan = c.post("/api/prices/plan", json={"scope": "all", "in_stock_only": False}).json()
    assert plan["products"] == 2 and plan["calls"] == 1


def test_unmatched_calibers_are_reported_and_a_bad_key_stops_the_job(authed, monkeypatch):
    c = authed
    make_product(c)
    plan = c.post("/api/prices/plan", json={"scope": "all", "in_stock_only": False}).json()
    assert plan["unmatched"] and plan["calls"] == 0

    def reject(endpoint, params, key):
        raise pricing.PriceError("The price service did not accept the API key.", fatal=True)

    monkeypatch.setattr(pricing, "fetch", reject)
    monkeypatch.setattr(pricing, "_pause", lambda s: None)
    c.put("/api/prices/settings", json={"key": "bad", "per_minute": 250})
    cal = c.get("/api/calibers").json()[0]
    c.put(f"/api/prices/calibers/{cal['id']}", json={"slug": "9mm-luger"})
    job = c.post("/api/prices/jobs", json={"scope": "all", "in_stock_only": False}).json()
    done = wait_done(c, job)
    assert done["state"] == "stopped" and "API key" in done["message"]


def test_the_api_key_stays_out_of_backups_and_survives_a_restore(authed):
    c = authed
    c.put("/api/prices/settings", json={"key": "secret-key", "per_minute": 5})
    backup = c.get("/api/backup").content
    path = Path(config.DB_PATH).parent / "keycheck.db"
    path.write_bytes(backup)
    con = sqlite3.connect(path)
    assert con.execute("SELECT count(*) FROM settings WHERE key='price_api_key'").fetchone()[0] == 0
    con.close()
    c.put("/api/prices/settings", json={"key": "newer-key", "per_minute": 5})
    assert c.post("/api/restore", content=backup).status_code == 200
    assert c.get("/api/prices/config").json()["key_set"] is True
    assert c.delete("/api/prices/key").status_code == 200
    assert c.get("/api/prices/config").json()["key_set"] is False


def test_editing_a_products_cost_stamps_its_price_date(authed):
    c = authed
    p = make_product(c)
    assert p["price_updated"] is None
    body = {k: p[k] for k in ("caliber_id", "brand", "name", "bullet_weight_gr", "bullet_type", "rounds_per_box")}
    stamped = c.put(f"/api/products/{p['id']}", json={**body, "cost_per_box": 12.5}).json()
    assert stamped["price_updated"]
    assert c.put(f"/api/products/{p['id']}", json={**body, "cost_per_box": None}).json()["price_updated"] is None
