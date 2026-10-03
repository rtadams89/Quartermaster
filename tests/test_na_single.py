from tests.conftest import make_product, run_batch


def stock(c, p, code, n):
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, n)])


def test_weight_na_is_stored_shown_and_not_flagged_as_missing(authed):
    p = make_product(authed, name="Tracer", bullet_weight_gr=0, bullet_type="Tracer", cost_per_box=20)
    assert p["weight_na"] is True and p["bullet_weight_gr"] == 0
    assert "gr" not in p["spec"]                                            # nothing to print for the weight
    assert authed.get("/api/needs-details").json()["products"] == []         # N/A is an answer
    blank = make_product(authed, name="Blank", bullet_weight_gr=None, cost_per_box=20)
    assert blank["weight_na"] is False
    miss = authed.get("/api/needs-details").json()["products"]
    assert [(m["name"], m["missing"]) for m in miss] == [("Blank", ["bullet weight"])]


def test_drill_groups_na_between_the_weights_and_not_listed(authed):
    for name, w in [("Heavy", 124), ("Light", 115), ("Na", 0), ("Unknown", None)]:
        stock(authed, make_product(authed, name=name, bullet_weight_gr=w), {"Heavy": "012345678905", "Light": "036000291452", "Na": "096619756803", "Unknown": "042100005264"}[name], 1)
    cid = authed.get("/api/calibers").json()[0]["id"]
    rows = authed.get(f"/api/inventory/drill?caliber={cid}").json()["rows"]
    assert [(r["label"], r["key"]) for r in rows] == [("115 gr", "115"), ("124 gr", "124"), ("N/A", "na"), ("No weight listed", "none")]
    deeper = authed.get(f"/api/inventory/drill?caliber={cid}&weight=na").json()
    assert deeper["breadcrumb"][-1] == "N/A" and [r["label"] for r in deeper["rows"]] == ["Federal Na"]


def test_na_round_trips_through_the_product_csv(authed):
    make_product(authed, name="Tracer", bullet_weight_gr=0)
    csv = authed.get("/api/export/products.csv").text
    assert ",N/A," in csv
    r = authed.post("/api/import/products?apply=false", content=csv.encode())
    assert r.status_code == 200 and (r.json()["unchanged"], r.json()["create"], r.json()["error_count"]) == (1, 0, 0)
    new = "caliber,manufacturer,name,weight_gr,rounds_per_box\n9mm Luger,Acme,Flare,n/a,10\n"
    assert authed.post("/api/import/products?apply=true", content=new.encode()).json()["applied"]
    assert next(p for p in authed.get("/api/products").json() if p["name"] == "Flare")["bullet_weight_gr"] == 0


def test_single_round_products_have_rounds_not_boxes_in_summaries(authed):
    box = make_product(authed, name="Boxed", rounds_per_box=50)
    loose = make_product(authed, name="Loose", rounds_per_box=1)
    stock(authed, box, "012345678905", 2)
    stock(authed, loose, "036000291452", 30)
    assert loose["single"] is True and "by the round" in loose["spec"] and "rd/box" not in loose["spec"]
    top = authed.get("/api/inventory/drill").json()
    cal = top["rows"][0]
    assert cal["boxes"] == 2 and cal["rounds"] == 130                         # the 30 loose rounds are rounds only
    assert top["total_boxes"] == 2 and top["total_rounds"] == 130
    items = authed.get("/api/inventory/items").json()
    assert items["total_boxes"] == 2 and items["total_rounds"] == 130
    prods = authed.get(f"/api/inventory/drill?caliber={cal['key']}&weight=115").json()
    row = {r["label"]: r for r in prods["rows"]}["Federal Loose"]
    assert row["single"] is True and row["rounds"] == 30 and prods["total_boxes"] == 2
    assert authed.get("/api/inventory/code/036000291452").json()["single"] is True


def test_finishing_a_batch_reports_boxes_and_single_rounds_separately(authed):
    box = make_product(authed, name="Boxed", rounds_per_box=50)
    loose = make_product(authed, name="Loose", rounds_per_box=1)
    authed.post(f"/api/products/{box['id']}/barcodes", json={"code": "012345678905"})
    authed.post(f"/api/products/{loose['id']}/barcodes", json={"code": "036000291452"})
    r = run_batch(authed, "in", [("012345678905", 3), ("036000291452", 25)]).json()
    assert r["boxes"] == 3 and r["rounds"] == 25 and r["items"] == 2
    tx = {t["code"]: t["single"] for t in authed.get("/api/transactions").json()}
    assert tx == {"012345678905": False, "036000291452": True}
