from tests.conftest import make_product, run_batch


def stock(c, p, code, boxes):
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, boxes)])


def row(rows, label):
    return next(r for r in rows if r["label"] == label)


def test_cost_per_round_on_products_and_inventory(authed):
    p = make_product(authed, name="A", rounds_per_box=50, cost_per_box=18.5)
    q = make_product(authed, name="B")                           # no cost entered
    assert p["cost_per_round"] == 0.37 and q["cost_per_round"] is None
    stock(authed, p, "012345678905", 2)
    inv = authed.get("/api/inventory/items").json()
    assert inv["price"] == {"low": 0.37, "high": 0.37}
    assert inv["products"][0]["cost_per_round"] == 0.37


def test_range_per_caliber_weight_and_item_ignores_empty_and_uncosted(authed):
    cheap = make_product(authed, name="Cheap", bullet_weight_gr=115, rounds_per_box=50, cost_per_box=15)      # 0.30
    dear = make_product(authed, name="Dear", bullet_weight_gr=124, rounds_per_box=20, cost_per_box=11)        # 0.55
    free = make_product(authed, name="NoCost", bullet_weight_gr=115, rounds_per_box=50)
    gone = make_product(authed, name="Gone", bullet_weight_gr=115, rounds_per_box=50, cost_per_box=100)       # 2.00, zero stock
    for i, (p, code) in enumerate([(cheap, "012345678905"), (dear, "036000291452"), (free, "096619756803"), (gone, "042100005264")]):
        stock(authed, p, code, 1)
    run_batch(authed, "out", [("042100005264", 1)])
    top = authed.get("/api/inventory/drill").json()
    cal = next(r for r in top["rows"] if r["label"] == "9mm Luger")
    assert cal["price"] == {"low": 0.3, "high": 0.55}
    cid = cal["key"]
    weights = authed.get(f"/api/inventory/drill?caliber={cid}").json()["rows"]
    assert {w["label"]: w["price"] for w in weights} == {
        "115 gr": {"low": 0.3, "high": 0.3}, "124 gr": {"low": 0.55, "high": 0.55}}
    items = authed.get(f"/api/inventory/drill?caliber={cid}&weight=115").json()["rows"]
    assert row(items, "Federal Cheap")["price"] == {"low": 0.3, "high": 0.3}
    assert row(items, "Federal NoCost")["price"] is None
