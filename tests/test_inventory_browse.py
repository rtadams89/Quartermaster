from tests.conftest import make_product, run_batch


def stock(c, p, code, boxes):
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, boxes)])


def setup(c):
    a = make_product(c, name="A", rounds_per_box=50, cost_per_box=18.5)               # Federal 115 gr
    b = make_product(c, brand="Winchester", name="B", rounds_per_box=100, cost_per_box=30)  # 115 gr, other maker
    free = make_product(c, name="NoCost", rounds_per_box=50)                            # Federal 115 gr, no cost
    bare = make_product(c, brand="", name="Bare", rounds_per_box=50, cost_per_box=10)   # no manufacturer
    other = make_product(c, name="Heavy", bullet_weight_gr=124, rounds_per_box=50, cost_per_box=20)
    for p, code, n in [(a, "012345678905", 2), (b, "036000291452", 1), (free, "096619756803", 3),
                       (bare, "042100005264", 1), (other, "073333333339", 1)]:
        stock(c, p, code, n)
    return a, b, free, bare, other


def test_value_per_row_and_overall_total_counts_only_costed_stock(authed):
    setup(authed)
    top = authed.get("/api/inventory/drill").json()
    cal = next(r for r in top["rows"] if r["label"] == "9mm Luger")
    # 2 x 18.50 + 1 x 30 + 1 x 10 + 1 x 20; the 3 uncosted boxes of one product are left out
    assert cal["value"] == 97.0 and cal["unpriced"] == 1
    assert top["total_value"] == 97.0 and top["unpriced"] == 1


def test_value_ignores_zero_stock(authed):
    p = make_product(authed, name="Gone", cost_per_box=25)
    stock(authed, p, "012345678905", 1)
    run_batch(authed, "out", [("012345678905", 1)])
    top = authed.get("/api/inventory/drill").json()
    assert top["total_value"] == 0 and top["rows"] == []


def test_manufacturer_level_sits_between_weight_and_product(authed):
    a, b, free, bare, other = setup(authed)
    cid = a["caliber_id"]
    rows = authed.get(f"/api/inventory/drill?caliber={cid}&weight=115&by_manufacturer=true").json()
    assert rows["level"] == "manufacturer"
    assert rows["breadcrumb"] == ["9mm Luger", "115 gr"]
    assert [r["label"] for r in rows["rows"]] == ["Federal", "Winchester", "No manufacturer"]   # no manufacturer last
    fed = rows["rows"][0]
    assert fed["boxes"] == 5 and fed["rounds"] == 250 and fed["value"] == 37.0 and fed["unpriced"] == 1
    assert fed["price"] == {"low": 0.37, "high": 0.37} and fed["drillable"]

    prods = authed.get(f"/api/inventory/drill?caliber={cid}&weight=115&manufacturer=Federal&by_manufacturer=true").json()
    assert prods["level"] == "product" and prods["breadcrumb"] == ["9mm Luger", "115 gr", "Federal"]
    assert [r["label"] for r in prods["rows"]] == ["Federal A", "Federal NoCost"]
    first = prods["rows"][0]
    assert first["item"]["codes"][0]["code"] == "012345678905" and first["item"]["cost_per_box"] == 18.5
    assert first["value"] == 37.0

    none = authed.get(f"/api/inventory/drill?caliber={cid}&weight=115&manufacturer=(none)&by_manufacturer=true").json()
    assert none["breadcrumb"][-1] == "No manufacturer" and [r["label"] for r in none["rows"]] == ["Bare"]


def test_kiosk_drill_still_goes_weight_to_product(authed):
    a, *_ = setup(authed)
    r = authed.get(f"/api/inventory/drill?caliber={a['caliber_id']}&weight=115").json()
    assert r["level"] == "product" and len(r["rows"]) == 4


def test_items_report_total_value(authed):
    setup(authed)
    assert authed.get("/api/inventory/items").json()["total_value"] == 97.0
