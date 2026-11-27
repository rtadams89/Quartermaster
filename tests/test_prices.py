import sqlite3
import time
from pathlib import Path

import pytest

from app import config, pricing
from tests.conftest import make_product, run_batch


def listing(descr, mfg, grains, price, count, condition="new", shipping="free", limit="-"):
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
    assert not pricing.skip_reason({"brand": "Surplus", "is_blank": True})
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
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: None)
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
    p = make_product(c, cost_per_box=20.0)
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
    assert by_id[p["id"]]["current"] == pytest.approx(0.4)
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
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: None)
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


def test_query_carries_the_fixed_filters_blank_flag_case_and_words():
    q = pricing.query_for(FEDERAL)
    assert q["condition"] == "new" and q["hide_purchase_limits"] == "true" and q["shipping_rating"] == "high"
    assert q["show_blanks"] == "false" and q["include"] == "Federal" and q["grains"] == 115 and q["casing"] == "" and q["exclude"] == ""
    blank = pricing.query_for({**FEDERAL, "is_blank": True, "case_material": "Brass"}, {"include": "shot", "exclude": "buck slug"})
    assert blank["show_blanks"] == "true" and blank["casing"] == "brass" and blank["include"] == "Federal shot" and blank["exclude"] == "buck slug"
    assert pricing.query_for({**FEDERAL, "case_material": "Plastic hull"})["casing"] == ""
    assert pricing.query_for({**FEDERAL, "case_material": "Composite"})["casing"] == "composite"
    assert pricing.search_key("9mm-luger", q) != pricing.search_key("9mm-luger", blank)


def test_estimate_checks_case_blank_and_must_words_and_explains_itself():
    steel = FEDERAL | {"case_material": "Steel"}
    items = [listing("Federal American Eagle 9mm 115gr FMJ steel", "Federal", 115, "$20.00", 50) | {"casing": "steel"},
             listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$30.00", 50) | {"casing": "brass"},
             listing("Federal American Eagle 9mm 115gr FMJ blank", "Federal", 115, "$10.00", 50) | {"casing": "steel"}]
    trace: list[str] = []
    r = pricing.estimate(steel, items, trace=trace)
    assert r["price"] == pytest.approx(0.40) and r["listings"] == 1
    text = "\n".join(trace)
    assert "case is brass, not steel" in text and "listing is a blank but the product is not" in text and "usable" in text and "chosen" in text

    blanks = FEDERAL | {"is_blank": True, "bullet_type": ""}
    assert pricing.estimate(blanks, items)["price"] == pytest.approx(0.20)
    assert pricing.estimate(FEDERAL, items, {"include": "steel"})["listings"] == 1
    assert pricing.estimate(FEDERAL, items, {"exclude": "steel"})["listings"] == 1
    assert pricing.estimate(FEDERAL, items, {"include": "birdshot"})["confidence"] == "none"


def test_a_rejected_case_filter_is_retried_without_it(monkeypatch):
    seen = []

    def fetch(endpoint, params, key):
        seen.append(dict(params))
        if "casing" in params:
            raise pricing.PriceError("bad option", stale=True)
        return {"status": "success", "data": {"items": [listing("x", "Federal", 115, "$1.00", 50)], "total": 1, "filtered": 1}}

    monkeypatch.setattr(pricing, "fetch", fetch)
    log: list[str] = []
    items = pricing.search("k", "9mm-luger", pricing.query_for(FEDERAL | {"case_material": "brass"}), log)
    assert len(items) == 1 and "casing" in seen[0] and "casing" not in seen[1]
    assert sum("REQUEST" in x for x in log) == 2 and any("without the case-material filter" in x for x in log)
    with pytest.raises(pricing.PriceError) as bad:
        pricing._checked({"status": "error", "message": "stale_input"})
    assert bad.value.stale


def test_words_blank_and_case_reach_the_search_and_the_log_is_available(authed, fake_service):
    c = authed
    c.put("/api/prices/settings", json={"key": "secret-key", "per_minute": 250})
    cal = c.get("/api/calibers").json()[0]
    c.put(f"/api/prices/calibers/{cal['id']}", json={"slug": "9mm-luger", "include": "  eagle ", "exclude": "buck  slug"})
    c.put(f"/api/prices/calibers/{cal['id']}", json={"exclude": "buck slug blank"})
    row = [x for x in c.get("/api/prices/config").json()["calibers"] if x["id"] == cal["id"]][0]
    assert row["slug"] == "9mm-luger" and row["include"] == "eagle" and row["exclude"] == "buck slug blank"
    p = make_product(c, case_material=" Brass ")
    assert p["case_material"] == "Brass" and p["is_blank"] is False
    blank = make_product(c, name="Blanks", is_blank=True, case_material="Steel")
    assert blank["is_blank"] is True

    job = c.post("/api/prices/jobs", json={"scope": "caliber", "id": cal["id"], "in_stock_only": False}).json()
    done = wait_done(c, job)
    assert done["calls"] == 2
    searches = [x[1] for x in fake_service if x[0] == "search_ammo"]
    assert {s["show_blanks"] for s in searches} == {"true", "false"} and {s.get("casing") for s in searches} == {"brass", "steel"}
    assert all(s["include"].endswith(" eagle") and len(s["include"].split()) == 2 and s["exclude"] == "buck slug blank" and s["condition"] == "new" and s["hide_purchase_limits"] == "true"
               and s["shipping_rating"] == "high" and s["length"] == 40 and s["start"] == 0 for s in searches)

    log = c.get(f"/api/prices/jobs/{job['id']}/log")
    assert log.status_code == 200 and log.headers["content-type"].startswith("text/plain")
    assert "secret-key" not in log.text and "REQUEST search_ammo" in log.text and "RESULT:" in log.text and "Federal American Eagle" in log.text
    assert "usable" in log.text and "chosen" in log.text
    assert c.get("/api/prices/jobs/nope/log").status_code == 404

    c.put(f"/api/prices/calibers/{cal['id']}", json={"include": "", "exclude": ""})
    row = [x for x in c.get("/api/prices/config").json()["calibers"] if x["id"] == cal["id"]][0]
    assert row["include"] == "" and row["exclude"] == ""


def test_case_material_and_blank_are_saved_exported_and_imported(authed):
    c = authed
    plain = make_product(c)
    assert plain["case_material"] == "" and plain["is_blank"] is False
    p = make_product(c, name="Blanks", case_material="Brass", is_blank=True)
    csv_text = c.get("/api/export/products.csv").text
    assert csv_text.splitlines()[0].endswith(",case_material,blank") and ",Brass,yes" in csv_text
    r = c.post("/api/import/products", content=csv_text.encode()).json()
    assert (r["update"], r["unchanged"], r["error_count"]) == (0, 2, 0)
    edited = csv_text.replace(",Brass,yes", ",Steel,no")
    assert c.post("/api/import/products?apply=true", content=edited.encode()).status_code == 200
    after = {x["id"]: x for x in c.get("/api/products").json()}[p["id"]]
    assert after["case_material"] == "Steel" and after["is_blank"] is False
    assert c.post("/api/import/products", content=csv_text.replace(",Brass,yes", ",Brass,maybe").encode()).json()["error_count"] == 1
    bad = c.post("/api/import/products", content=csv_text.replace(",Brass,yes", ",Plastic,yes").encode()).json()
    assert bad["error_count"] == 1 and bad["errors"][0]["error"] == "case_material must be one of: Brass, Steel, Aluminum, Composite, Alloy"
    lower = c.post("/api/import/products?apply=true", content=csv_text.replace(",Brass,yes", ",ALLOY,yes").encode())
    assert lower.status_code == 200 and {x["id"]: x for x in c.get("/api/products").json()}[p["id"]]["case_material"] == "Alloy"


def test_case_material_must_be_one_of_the_ammoseek_choices(authed):
    cal = authed.get("/api/calibers").json()[0]["id"]
    body = {"caliber_id": cal, "brand": "X", "name": "Y", "bullet_weight_gr": 115, "bullet_type": "FMJ", "rounds_per_box": 50}
    assert authed.post("/api/products", json={**body, "case_material": "Plastic"}).status_code == 422
    made = authed.post("/api/products", json={**body, "case_material": " composite "})
    assert made.status_code == 200 and made.json()["case_material"] == "Composite"
    assert authed.put(f"/api/products/{made.json()['id']}", json={**body, "case_material": ""}).json()["case_material"] == ""


def test_a_backup_from_before_case_material_and_blank_still_restores(authed, tmp_path):
    p = make_product(authed, name="Old", case_material="Brass", is_blank=True)
    src = tmp_path / "full.db"
    src.write_bytes(authed.get("/api/backup").content)
    con = sqlite3.connect(src)
    con.execute("ALTER TABLE products DROP COLUMN case_material")
    con.execute("ALTER TABLE products DROP COLUMN is_blank")
    con.commit()
    con.close()
    make_product(authed, name="Newer")
    assert authed.post("/api/restore", content=src.read_bytes()).status_code == 200
    prods = authed.get("/api/products").json()
    assert [x["name"] for x in prods] == ["Old"] and prods[0]["case_material"] == "" and prods[0]["is_blank"] is False


def test_startup_upgrade_adds_case_material_and_blank(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    import app.db as db

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as con:
        con.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        con.execute(text("INSERT INTO products (id, name) VALUES (1, 'x')"))
    monkeypatch.setattr(db, "engine", eng)
    db.upgrade_schema()
    with eng.begin() as con:
        assert con.execute(text("SELECT case_material, is_blank FROM products")).one() == ("", 0)


def test_conditionally_free_shipping_is_read_by_its_score():
    assert pricing.good_shipping({"shipping_rating": "cf 76"}) and pricing.good_shipping({"shipping_rating": "CF 50"})
    assert not pricing.good_shipping({"shipping_rating": "cf 49"}) and not pricing.good_shipping({"shipping_rating": "cf"})


def test_one_round_listings_and_prices_far_from_the_current_one_are_ignored():
    sterling = {"brand": "Sterling", "name": "", "grain": 115, "bullet_type": "FMJ", "rounds_per_box": 50, "current": 0.3994, "case_material": "Brass"}
    items = [
        {"retailer": "G", "descr": "Sterling 9mm Luger Brass FMJ 115 Grain 50 Rounds", "mfg": "Sterling", "grains": 115, "count": 50, "price": "$15.95",
         "shipping_rating": "cf 76", "purchaselimit": "-", "casing": "brass", "condition": "new"},
        {"retailer": "G", "descr": "Sterling 9mm Luger Brass FMJ 115 Grain 800 Rounds", "mfg": "Sterling", "grains": 115, "count": 800, "price": "$255.20",
         "shipping_rating": "free", "purchaselimit": "-", "casing": "brass", "condition": "new"},
        {"retailer": "O", "descr": "Sterling 9x19mm Luger 115 Grain FMJ Brass", "mfg": "Sterling", "grains": 115, "count": 1, "price": "$17.50",
         "shipping_rating": "71", "purchaselimit": "-", "casing": "brass", "condition": "new"},
        {"retailer": "O", "descr": "Sterling 9x19mm Luger 115 Grain FMJ Brass", "mfg": "Sterling", "grains": 115, "count": 1, "price": "$350.00",
         "shipping_rating": "71", "purchaselimit": "-", "casing": "brass", "condition": "new"},
    ]
    trace: list[str] = []
    r = pricing.estimate(sterling, items, trace=trace)
    assert r["price"] == pytest.approx(0.3315) and r["listings"] == 2
    assert "counts 1 round" in "\n".join(trace)
    # loose products accept a count of 1; the price band rejects the wrong per-round price
    loose = sterling | {"rounds_per_box": 1}
    assert "counts 1 round" not in "\n".join(_trace(loose, items[2:3]))
    items[3]["count"] = 50
    assert pricing.estimate(sterling, items[:1] + items[3:4])["listings"] == 1
    far = pricing.estimate(sterling, items[3:4])
    assert far["price"] is None and "half or over double" in far["note"]


def _trace(product, items):
    trace: list[str] = []
    pricing.estimate(product, items, trace=trace)
    return trace


def test_without_a_current_price_outliers_are_dropped_by_the_median():
    items = [listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, f"${p}", 50) for p in (20, 21, 22, 23, 90)]
    trace: list[str] = []
    assert pricing.estimate(FEDERAL, items, trace=trace)["price"] == pytest.approx(0.43, abs=0.01)
    assert "no current price" in "\n".join(trace)
    assert pricing.estimate(FEDERAL | {"current": 0.44}, items)["listings"] == 5 - 1


def test_blank_products_with_no_brand_are_priced_from_all_blank_listings(authed, fake_service, monkeypatch):
    surplus_blank = {"brand": "Surplus", "name": "", "grain": 0, "bullet_type": "Blanks", "is_blank": True, "rounds_per_box": 1}
    q = pricing.query_for(surplus_blank)
    assert q["include"] == "blank" and q["show_blanks"] == "true" and q["grains"] is None
    items = [listing(f"5.56 NATO blank cartridges {n}", mk, 0, f"${p}", 50) for n, mk, p in ((1, "A", 20), (2, "B", 25), (3, "C", 30))]
    items.append(listing("5.56 NATO ball 55gr", "D", 55, "$5.00", 50))
    r = pricing.estimate(surplus_blank, items)
    assert r["price"] == pytest.approx(0.5) and r["listings"] == 3 and r["confidence"] == "medium" and "all blank listings" in r["note"]

    c = authed
    c.put("/api/prices/settings", json={"key": "secret-key", "per_minute": 250})
    cal = c.get("/api/calibers").json()[0]
    c.put(f"/api/prices/calibers/{cal['id']}", json={"slug": "9mm-luger"})
    make_product(c, brand="Surplus", name="", bullet_weight_gr=0, bullet_type="Blanks", rounds_per_box=1, is_blank=True)
    make_product(c, brand="Surplus", name="Loose", bullet_weight_gr=0, rounds_per_box=1)
    plan = c.post("/api/prices/plan", json={"scope": "all", "in_stock_only": False}).json()
    assert plan["products"] == 2 and plan["calls"] == 1
    assert [s["label"] for s in plan["skipped"]] == ["Surplus Loose"]


def test_an_unexpected_failure_does_not_expose_its_details(authed, fake_service, monkeypatch):
    c = authed
    c.put("/api/prices/settings", json={"key": "secret-key", "per_minute": 250})
    cal = c.get("/api/calibers").json()[0]
    c.put(f"/api/prices/calibers/{cal['id']}", json={"slug": "9mm-luger"})
    make_product(c)

    def boom(*args, **kwargs):
        raise RuntimeError("/srv/secret/path.py exploded")

    monkeypatch.setattr(pricing, "estimate", boom)
    job = c.post("/api/prices/jobs", json={"scope": "all", "in_stock_only": False}).json()
    done = wait_done(c, job)
    assert done["state"] == "stopped" and "secret" not in done["message"] and "secret/path" not in c.get(f"/api/prices/jobs/{job['id']}/log").text


def test_stale_scope_lists_products_not_updated_in_90_days_and_ids_narrow_a_run(authed, fake_service):
    from datetime import timedelta

    from app.db import SessionLocal, utcnow
    from app.models import Product

    c = authed
    c.put("/api/prices/settings", json={"key": "secret-key", "per_minute": 250})
    cal = c.get("/api/calibers").json()[0]
    c.put(f"/api/prices/calibers/{cal['id']}", json={"slug": "9mm-luger"})
    fresh = make_product(c, name="Fresh", cost_per_box=20.0)
    never = make_product(c, name="Never")
    old = make_product(c, name="Old", cost_per_box=20.0)
    stale_edge = make_product(c, name="Edge", cost_per_box=20.0)
    unpriced = make_product(c, brand="Surplus", name="Loose", bullet_weight_gr=0, rounds_per_box=1)
    with SessionLocal() as db:
        db.get(Product, old["id"]).price_updated = utcnow() - timedelta(days=91)
        db.get(Product, stale_edge["id"]).price_updated = utcnow() - timedelta(days=89)
        db.commit()

    plan = c.post("/api/prices/plan", json={"scope": "stale", "in_stock_only": False}).json()
    by_name = {i["label"]: i for i in plan["items"]}
    assert set(by_name) == {"Federal Never", "Federal Old", "Surplus Loose"}
    assert by_name["Federal Never"]["price_updated"] is None and by_name["Federal Old"]["price_updated"]
    assert by_name["Federal Never"]["note"] is None and by_name["Federal Never"]["key"]
    assert by_name["Surplus Loose"]["note"] and by_name["Surplus Loose"]["key"] is None
    assert plan["per_minute"] == 250

    only = c.post("/api/prices/plan", json={"scope": "stale", "in_stock_only": False, "ids": [never["id"]]}).json()
    assert [i["label"] for i in only["items"]] == ["Federal Never"] and only["calls"] == 1

    assert c.post("/api/prices/jobs", json={"scope": "stale", "in_stock_only": False, "ids": []}).status_code == 400
    job = c.post("/api/prices/jobs", json={"scope": "stale", "in_stock_only": False, "ids": [never["id"], old["id"]]}).json()
    done = wait_done(c, job)
    assert sorted(r["label"] for r in done["results"]) == ["Federal Never", "Federal Old"]
    assert fresh["id"] and unpriced["id"]


def _limited(times, retry_after=None):
    calls = {"n": 0}

    def fake(endpoint, params, key):
        calls["n"] += 1
        if calls["n"] <= times:
            raise pricing.PriceError("limit", rate_limited=True, retry_after=retry_after)
        return {"items": [listing("Federal 9mm", "Federal", 115, "$20.00", 50)]}

    return fake, calls


def test_a_rate_limit_backs_off_20_then_40_seconds_and_then_succeeds(monkeypatch):
    fake, calls = _limited(2)
    pauses = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: pauses.append(s))
    assert len(pricing.search("k", "9mm-luger", {})) == 1
    assert pauses == [20, 40] and calls["n"] == 3


def test_a_rate_limit_honours_retry_after(monkeypatch):
    fake, _ = _limited(2, retry_after=7)
    pauses = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: pauses.append(s))
    pricing.search("k", "9mm-luger", {})
    assert pauses == [7, 7]


def test_a_rate_limit_that_never_clears_stops_after_three_waits(monkeypatch):
    fake, calls = _limited(99)
    pauses = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: pauses.append(s))
    with pytest.raises(pricing.PriceError) as err:
        pricing.search("k", "9mm-luger", {})
    assert err.value.rate_limited and "did not clear" in str(err.value)
    assert pauses == [20, 40, 60] and calls["n"] == 4


def test_pacing_restarts_after_a_retry(monkeypatch):
    clock = {"t": 1000.0}
    pauses = []

    def pause(s, c=None):
        pauses.append(s)
        clock["t"] += s

    state = {"n": 0}

    def fake_search(key, slug, query, log, cancel=None):
        state["n"] += 1
        if state["n"] == 2:
            clock["t"] += 20
        return [listing("Federal 9mm", "Federal", 115, "$20.00", 50)]

    monkeypatch.setattr(pricing, "time", type("T", (), {"monotonic": staticmethod(lambda: clock["t"])}))
    monkeypatch.setattr(pricing, "_pause", pause)
    monkeypatch.setattr(pricing, "search", fake_search)
    products = [{"id": i, "label": f"P{i}", "brand": "Federal", "name": f"N{i}", "grain": 100 + i, "bullet_type": "FMJ",
                 "slug": "9mm-luger", "words": {}, "current": None} for i in (1, 2, 3)]
    job = pricing.Job(products, "k", 1)
    pricing._run(job)
    assert pauses == [60, 60]


def test_must_include_words_are_added_to_the_brand_not_a_replacement():
    q = pricing.query_for({**FEDERAL, "brand": "Challenger"}, {"include": "shot"})
    assert q["include"] == "Challenger shot"
    assert pricing.query_for({"brand": "", "is_blank": True}, {})["include"] == "blank"


def test_every_listing_the_service_sends_is_used(monkeypatch):
    many = [listing("Federal 9mm", "Federal", 115, "$20.00", 50) for _ in range(100)]
    monkeypatch.setattr(pricing, "fetch", lambda e, p, k: {"items": many})
    assert len(pricing.search("k", "9mm-luger", {})) == 100


def test_cancel_interrupts_a_rate_limit_wait(monkeypatch):
    fake, calls = _limited(99)
    cancel = pricing.threading.Event()

    def pause(s, c=None):
        cancel.set()

    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", pause)
    with pytest.raises(pricing.PriceError) as err:
        pricing.search("k", "9mm-luger", {}, cancel=cancel)
    assert str(err.value) == "Cancelled." and calls["n"] == 1


def test_the_service_reply_to_an_error_is_logged(monkeypatch):
    def fake(endpoint, params, key):
        raise pricing.PriceError("limit", rate_limited=True, retry_after=1, detail="HTTP 429 Too Many Requests; headers {}; body 'slow down'")

    log = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: None)
    with pytest.raises(pricing.PriceError):
        pricing.search("k", "9mm-luger", {}, log)
    assert any("service replied: HTTP 429" in x and "slow down" in x for x in log)


def test_a_long_retry_after_stops_at_once_without_retrying(monkeypatch):
    fake, calls = _limited(99, retry_after=53451)
    pauses = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: pauses.append(s))
    with pytest.raises(pricing.PriceError) as err:
        pricing.search("k", "9mm-luger", {})
    assert err.value.rate_limited and "14 h 51 min" in str(err.value)
    assert pauses == [] and calls["n"] == 1


def test_retry_after_is_read_whatever_the_header_case(monkeypatch):
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(429)
            self.send_header("retry-after", "53451")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    pricing.threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(pricing, "BASE_URL", f"http://127.0.0.1:{srv.server_port}/")
    with pytest.raises(pricing.PriceError) as err:
        pricing.fetch("x", {}, "k")
    srv.shutdown()
    assert err.value.retry_after == 53451


def test_a_daily_limit_stops_at_once_even_with_a_short_retry_after(monkeypatch):
    def fake(endpoint, params, key):
        raise pricing.PriceError("limit", rate_limited=True, retry_after=60, daily=True)

    pauses = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: pauses.append(s))
    with pytest.raises(pricing.PriceError) as err:
        pricing.search("k", "9mm-luger", {})
    assert "daily request limit" in str(err.value) and "1 min" in str(err.value) and pauses == []


def test_the_error_body_tells_a_daily_limit_from_a_burst_limit():
    assert pricing._error_info('{"error": {"limit_type": "daily", "retry_after": 5}, "status_code": 429}')["limit_type"] == "daily"
    assert pricing._error_info("not json") == {} and pricing._error_info("[]") == {}


def test_a_wait_over_five_minutes_aborts_with_when_to_retry(monkeypatch):
    fake, calls = _limited(99, retry_after=301)
    pauses = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: pauses.append(s))
    with pytest.raises(pricing.PriceError) as err:
        pricing.search("k", "9mm-luger", {})
    assert "5 min" in str(err.value) and "UTC" in str(err.value) and "by hand" in str(err.value)
    assert pauses == [] and calls["n"] == 1


def test_a_wait_of_five_minutes_or_less_is_waited_out(monkeypatch):
    fake, calls = _limited(1, retry_after=300)
    pauses = []
    monkeypatch.setattr(pricing, "fetch", fake)
    monkeypatch.setattr(pricing, "_pause", lambda s, c=None: pauses.append(s))
    assert len(pricing.search("k", "9mm-luger", {})) == 1 and pauses == [300]


def test_response_headers_record_the_daily_allowance():
    pricing.LIMITS.update({"daily_remaining": None, "credits_remaining": None, "seen": None})
    assert pricing.daily_left() is None
    pricing._note_limits({"X-RateLimit-Daily-Remaining": "42", "X-Credits-Remaining": "88.5"})
    assert pricing.daily_left() == 42 and pricing.LIMITS["credits_remaining"] == 88.5
    pricing.LIMITS["seen"] = pricing.LIMITS["seen"].replace(year=2000)
    assert pricing.daily_left() is None
    pricing._note_limits({})


def test_a_job_stops_before_searching_when_no_daily_requests_remain(monkeypatch):
    called = []
    monkeypatch.setattr(pricing, "search", lambda *a, **k: called.append(1) or [])
    pricing.LIMITS.update({"daily_remaining": 0, "seen": pricing.datetime.now(pricing.timezone.utc).date()})
    products = [{"id": 1, "label": "P", "brand": "Federal", "name": "N", "grain": 115, "bullet_type": "FMJ", "slug": "9mm-luger", "words": {}, "current": None}]
    job = pricing.Job(products, "k", 5)
    pricing._run(job)
    pricing.LIMITS.update({"daily_remaining": None, "seen": None})
    assert job.state == "stopped" and "daily request limit" in job.message and "UTC" in job.message and not called


def test_landed_cost_adds_shipping_unless_free_then_tax():
    base = {"price": "$20.00", "count": 50}
    assert pricing.landed({**base, "shipping_rating": "free"}) == pytest.approx(0.40)
    assert pricing.landed({**base, "shipping_rating": "55"}) == pytest.approx(0.425)
    assert pricing.landed({**base, "shipping_rating": "cf 76"}) == pytest.approx(0.425)
    assert pricing.landed({**base, "shipping_rating": "55"}, 8.6) == pytest.approx(0.425 * 1.086)
    assert pricing.landed({**base, "shipping_rating": "free"}, 10) == pytest.approx(0.44)
    assert pricing.landed({"price": "", "count": 0}) is None


def test_estimate_includes_shipping_and_tax():
    items = [listing("Federal American Eagle 9mm 115gr FMJ", "Federal", 115, "$20.00", 50, shipping=s) for s in ("free", "60", "free")]
    assert pricing.estimate(FEDERAL, items)["price"] == pytest.approx((0.4 + 0.425 + 0.4) / 3, abs=1e-4)
    assert pricing.estimate(FEDERAL, items, tax=10)["price"] == pytest.approx((0.4 + 0.425 + 0.4) / 3 * 1.1, abs=1e-4)


def test_the_tax_rate_is_saved_in_settings_and_used_by_jobs(authed):
    c = authed
    assert c.get("/api/prices/config").json()["tax_percent"] == 0
    assert c.put("/api/prices/settings", json={"key": "", "per_minute": 5, "tax_percent": 8.6}).json()["tax_percent"] == 8.6
    c.put("/api/prices/settings", json={"key": "", "per_minute": 5})
    assert c.get("/api/prices/config").json()["tax_percent"] == 8.6
    assert c.put("/api/prices/settings", json={"key": "", "per_minute": 5, "tax_percent": 99}).status_code == 422
    assert c.put("/api/prices/settings", json={"key": "", "per_minute": 5, "tax_percent": -1}).status_code == 422
