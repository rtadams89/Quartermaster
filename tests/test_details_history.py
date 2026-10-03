from tests.conftest import make_product, run_batch


def stock(c, p, code, boxes):
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, boxes)])


def test_needs_details_lists_unidentified_codes_and_products_missing_details(authed):
    full = make_product(authed, name="Full", cost_per_box=20)
    nocost = make_product(authed, name="NoCost")
    bare = make_product(authed, brand="", name="Bare", bullet_type="", bullet_weight_gr=None, cost_per_box=9)
    stock(authed, nocost, "012345678905", 2)
    run_batch(authed, "in", [("036000291452", 1)])                       # scanned, never described
    d = authed.get("/api/needs-details").json()
    assert d["count"] == 3
    assert [u["code"] for u in d["unidentified"]] == ["036000291452"]
    by = {p["name"]: p for p in d["products"]}
    assert "Full" not in by
    assert by["NoCost"]["missing"] == ["cost"] and by["NoCost"]["boxes"] == 2 and by["NoCost"]["codes"] == ["012345678905"]
    assert by["Bare"]["missing"] == ["manufacturer", "bullet type", "bullet weight"]
    assert full["id"] not in [p["id"] for p in d["products"]]


def test_shotshell_does_not_need_a_bullet_weight(authed):
    cal = authed.post("/api/calibers", json={"name": "20ga Test"}).json()["id"]
    make_product(authed, caliber_id=cal, name="Shells", bullet_weight_gr=None, bullet_type="1 oz #8 Birdshot", cost_per_box=9)
    assert authed.get("/api/needs-details").json()["products"] == []


def test_out_of_stock_calibers_are_ones_you_keep_but_have_none_of(authed):
    cals = {c["name"]: c["id"] for c in authed.get("/api/calibers").json()}
    gone = make_product(authed, name="Gone")                                # 9mm Luger product, nothing on hand
    stock(authed, gone, "012345678905", 1)
    run_batch(authed, "out", [("012345678905", 1)])
    alert = cals[".45 ACP"]
    authed.patch(f"/api/calibers/{alert}", json={"min_rounds": 100})        # an alert level counts as "kept"
    names = [c["name"] for c in authed.get("/api/out-of-stock").json()]
    assert "9mm Luger" in names and ".45 ACP" in names
    assert ".308 Win" not in " ".join(names)                                # never used: not "out"
    stock(authed, make_product(authed, name="Back"), "036000291452", 1)
    assert "9mm Luger" not in [c["name"] for c in authed.get("/api/out-of-stock").json()]


def test_drill_lists_out_of_stock_calibers_only_when_asked(authed):
    p = make_product(authed, name="Gone")
    stock(authed, p, "012345678905", 1)
    run_batch(authed, "out", [("012345678905", 1)])
    assert authed.get("/api/inventory/drill").json()["rows"] == []           # the kiosk still hides empties
    rows = authed.get("/api/inventory/drill?include_empty=true").json()["rows"]
    assert [(r["label"], r["out"], r["drillable"], r["boxes"]) for r in rows] == [("9mm Luger", True, False, 0)]


def test_clear_history_keeps_stock_and_everything_else(authed):
    a = make_product(authed, name="A", cost_per_box=10)
    b = make_product(authed, name="B")
    stock(authed, a, "012345678905", 5)
    stock(authed, b, "036000291452", 2)
    run_batch(authed, "out", [("036000291452", 2)])                          # B back to zero
    run_batch(authed, "out", [("012345678905", 1)])
    before = authed.get("/api/inventory/items").json()
    assert authed.post("/api/history/clear", json={"confirm": "nope"}).status_code == 400
    r = authed.post("/api/history/clear", json={"confirm": "CLEAR"}).json()
    assert r == {"removed": 4, "kept": 1}
    after = authed.get("/api/inventory/items").json()
    assert after["total_boxes"] == before["total_boxes"] == 4 and after["total_rounds"] == before["total_rounds"]
    log = authed.get("/api/transactions").json()
    assert len(log) == 1 and log[0]["boxes"] == 4 and log[0]["kind"] == "adjust" and "Opening balance" in log[0]["note"]
    assert len(authed.get("/api/products").json()) == 2                       # products and codes untouched


def test_clear_history_leaves_a_scan_session_in_progress(authed):
    a = make_product(authed, name="A")
    authed.post(f"/api/products/{a['id']}/barcodes", json={"code": "012345678905"})
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"})
    assert authed.post("/api/history/clear", json={"confirm": "CLEAR"}).status_code == 200
    assert authed.post(f"/api/batches/{b['id']}/finish").status_code == 200
    assert authed.get("/api/inventory/items").json()["total_boxes"] == 1
