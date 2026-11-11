from tests.conftest import make_product, run_batch


def start(c, kind="in"):
    return c.post("/api/batches", json={"kind": kind}).json()["id"]


def add(c, bid, pid, **kw):
    return c.post(f"/api/batches/{bid}/product", json={"product_id": pid, **kw})


def test_product_without_a_code_gets_a_label_code(authed):
    p = make_product(authed, rounds_per_box=1)
    bid = start(authed)
    r = add(authed, bid, p["id"]).json()
    code = r["item"]["code"]
    assert code.startswith("QM") and r["known"] and not r["new_code"]
    assert r["item"]["product"]["id"] == p["id"] and r["item"]["quantity"] == 1
    again = add(authed, bid, p["id"], quantity=4).json()
    assert again["item"]["code"] == code and again["item"]["quantity"] == 5
    assert authed.get("/api/products").json()[0]["codes"] == [code]


def test_existing_codes_are_reused(authed):
    upc = make_product(authed, name="Has UPC")
    authed.post(f"/api/products/{upc['id']}/barcodes", json={"code": "012345678905"})
    both = make_product(authed, name="Has both")
    authed.post(f"/api/products/{both['id']}/barcodes", json={"code": "036000291452"})
    label = authed.post("/api/labels/allocate", json={"count": 1, "product_id": both["id"]}).json()["codes"][0]
    bid = start(authed)
    assert add(authed, bid, upc["id"]).json()["item"]["code"] == "012345678905"
    assert add(authed, bid, both["id"]).json()["item"]["code"] == label


def test_browse_added_items_are_recorded_when_finished(authed):
    p = make_product(authed, rounds_per_box=1)
    bid = start(authed)
    add(authed, bid, p["id"], quantity=300)
    assert authed.post(f"/api/batches/{bid}/finish").json()["rounds"] == 300
    out = start(authed, "out")
    add(authed, out, p["id"], quantity=120)
    authed.post(f"/api/batches/{out}/finish")
    top = authed.get("/api/inventory/drill").json()["rows"]
    assert top[0]["rounds"] == 180


def test_add_product_errors(authed):
    p = make_product(authed)
    bid = start(authed)
    assert add(authed, bid, 9999).status_code == 404
    assert add(authed, bid, p["id"], quantity=0).status_code == 422
    authed.post(f"/api/batches/{bid}/cancel")
    assert add(authed, bid, p["id"]).status_code == 409


def test_drill_all_products_lists_what_is_not_in_stock(authed):
    stocked = make_product(authed, name="Stocked")
    empty = make_product(authed, name="Empty", bullet_weight_gr=124)
    authed.post(f"/api/products/{stocked['id']}/barcodes", json={"code": "012345678905"})
    run_batch(authed, "in", [("012345678905", 1), ("099999999990", 1)])
    top = authed.get("/api/inventory/drill").json()["rows"]
    assert [r["label"] for r in top] == ["9mm Luger", "Unidentified"]
    cal = authed.get("/api/inventory/drill?all_products=true").json()["rows"]
    assert [r["label"] for r in cal] == ["9mm Luger"]
    weights = authed.get(f"/api/inventory/drill?all_products=true&caliber={cal[0]['key']}").json()["rows"]
    assert [r["label"] for r in weights] == ["115 gr", "124 gr"]
    prods = authed.get(f"/api/inventory/drill?all_products=true&caliber={cal[0]['key']}&weight=124").json()["rows"]
    assert [(r["key"], r["boxes"]) for r in prods] == [(str(empty["id"]), 0)]
