import pytest

from app.codes import normalize_code
from tests.conftest import make_product, run_batch


@pytest.mark.parametrize(
    "raw,want",
    [
        ("012345678905", "012345678905"),       # UPC-A unchanged
        ("0012345678905", "012345678905"),      # EAN-13 form of the same UPC-A
        ("00012345678905", "012345678905"),     # GTIN-14 form
        ("5012345678900", "5012345678900"),     # real EAN-13 untouched
        ("  qm000123 ", "QM000123"),            # our own labels
    ],
)
def test_normalize(raw, want):
    assert normalize_code(raw) == want


def test_normalize_rejects_junk():
    for bad in ("", "   ", "has space", "x" * 100, "tab\tchar"):
        with pytest.raises(ValueError):
            normalize_code(bad)


def test_unknown_code_is_logged_then_identified_later(authed):
    """The core workflow: scan an unknown UPC, record it, describe it later."""
    r = run_batch(authed, "in", [("012345678905", 4)])
    assert r.status_code == 200 and r.json()["boxes"] == 4

    unid = authed.get("/api/inventory/unidentified").json()
    assert [(u["code"], u["boxes"], u["transactions"]) for u in unid] == [("012345678905", 4, 1)]

    p = make_product(authed)
    assert authed.put("/api/barcodes/012345678905", json={"product_id": p["id"]}).status_code == 200

    assert authed.get("/api/inventory/unidentified").json() == []
    items = authed.get("/api/inventory/items").json()
    assert items["total_boxes"] == 4 and items["total_rounds"] == 200  # history resolved retroactively


def test_batch_is_queued_until_finish(authed):
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"})
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"})
    assert authed.get("/api/inventory/items").json()["total_boxes"] == 0  # nothing recorded yet
    cur = authed.get("/api/batches/current").json()
    assert cur["items"][0]["quantity"] == 2
    item_id = cur["items"][0]["id"]
    authed.patch(f"/api/batches/{b['id']}/items/{item_id}", json={"quantity": 5})
    authed.post(f"/api/batches/{b['id']}/finish")
    assert authed.get("/api/inventory/items").json()["total_boxes"] == 5
    assert authed.get("/api/batches/current").json() is None
    assert authed.post(f"/api/batches/{b['id']}/finish").status_code == 409  # can't double-commit


def test_checkout_subtracts_and_zero_quantity_removes_item(authed):
    run_batch(authed, "in", [("111111111111", 10)])
    b = authed.post("/api/batches", json={"kind": "out"}).json()
    a = authed.post(f"/api/batches/{b['id']}/scan", json={"code": "111111111111"}).json()["item"]
    z = authed.post(f"/api/batches/{b['id']}/scan", json={"code": "222222222222"}).json()["item"]
    assert a["on_hand"] == 10
    authed.patch(f"/api/batches/{b['id']}/items/{z['id']}", json={"quantity": 0})
    authed.patch(f"/api/batches/{b['id']}/items/{a['id']}", json={"quantity": 3})
    assert authed.post(f"/api/batches/{b['id']}/finish").json()["boxes"] == 3
    assert authed.get("/api/inventory/items").json()["total_boxes"] == 7


def test_cannot_start_other_kind_over_nonempty_draft_but_can_over_empty(authed):
    authed.post("/api/batches", json={"kind": "in"})
    assert authed.post("/api/batches", json={"kind": "out"}).json()["kind"] == "out"  # empty draft replaced
    out = authed.get("/api/batches/current").json()
    authed.post(f"/api/batches/{out['id']}/scan", json={"code": "333333333333"})
    assert authed.post("/api/batches", json={"kind": "in"}).status_code == 409
    assert authed.post(f"/api/batches/{out['id']}/cancel").status_code == 200
    assert authed.get("/api/batches/current").json() is None


def test_empty_finish_rejected_and_bad_scan_rejected(authed):
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    assert authed.post(f"/api/batches/{b['id']}/finish").status_code == 400
    assert authed.post(f"/api/batches/{b['id']}/scan", json={"code": "has space"}).status_code == 400


def test_upca_and_ean13_scans_hit_the_same_item(authed):
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"})
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": "0012345678905"})
    items = authed.get("/api/batches/current").json()["items"]
    assert len(items) == 1 and items[0]["quantity"] == 2


def test_drilldown_caliber_weight_product(authed):
    cals = {c["name"]: c["id"] for c in authed.get("/api/calibers").json()}
    nine = cals["9mm Luger"]
    p115 = make_product(authed, caliber_id=nine, bullet_weight_gr=115, rounds_per_box=50)
    p124 = make_product(authed, caliber_id=nine, brand="Speer", name="Gold Dot", bullet_weight_gr=124, bullet_type="JHP", rounds_per_box=20)
    p40 = make_product(authed, caliber_id=cals[".40 S&W"], bullet_weight_gr=180, rounds_per_box=50)
    for p, code in ((p115, "100000000001"), (p124, "100000000002"), (p40, "100000000003")):
        authed.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(authed, "in", [("100000000001", 4), ("100000000002", 2), ("100000000003", 1), ("999999999999", 3)])

    top = authed.get("/api/inventory/drill").json()
    rows = {r["label"]: r for r in top["rows"]}
    assert rows["9mm Luger"]["rounds"] == 4 * 50 + 2 * 20 == 240
    assert rows[".40 S&W"]["rounds"] == 50
    assert rows["Unidentified"]["boxes"] == 3 and rows["Unidentified"]["rounds"] is None
    assert top["has_unknown_rounds"] is True

    w = authed.get(f"/api/inventory/drill?caliber={nine}").json()
    assert [(r["label"], r["rounds"]) for r in w["rows"]] == [("115 gr", 200), ("124 gr", 40)]
    assert w["breadcrumb"] == ["9mm Luger"]

    prod = authed.get(f"/api/inventory/drill?caliber={nine}&weight=124").json()
    assert prod["level"] == "product" and prod["breadcrumb"] == ["9mm Luger", "124 gr"]
    assert prod["rows"][0]["label"] == "Speer Gold Dot"
    assert "100000000002" in prod["rows"][0]["sublabel"] and prod["rows"][0]["boxes"] == 2

    unid = authed.get("/api/inventory/drill?caliber=unidentified").json()
    assert unid["rows"][0]["label"] == "999999999999"


def test_adjustment_and_history(authed):
    run_batch(authed, "in", [("111111111111", 5)])
    assert authed.post("/api/adjustments", json={"code": "111111111111", "boxes": -2, "note": "miscount"}).status_code == 200
    assert authed.post("/api/adjustments", json={"code": "111111111111", "boxes": 0}).status_code == 400
    assert authed.post("/api/adjustments", json={"code": "000000000000", "boxes": 1}).status_code == 404
    assert authed.get("/api/inventory/items").json()["total_boxes"] == 3
    kinds = [t["kind"] for t in authed.get("/api/transactions").json()]
    assert kinds == ["adjust", "in"]
    assert authed.get("/api/transactions?kind=in").json()[0]["boxes"] == 5


def test_caliber_management(authed):
    new = authed.post("/api/calibers", json={"name": "  .50 BMG "}).json()
    assert new["name"] == ".50 BMG"
    assert authed.post("/api/calibers", json={"name": ".50 bmg"}).status_code == 409
    authed.patch(f"/api/calibers/{new['id']}", json={"name": ".50 BMG (M33)"})
    ids = [c["id"] for c in authed.get("/api/calibers").json()]
    authed.post("/api/calibers/reorder", json={"ids": list(reversed(ids))})
    assert authed.get("/api/calibers").json()[0]["id"] == ids[-1]
    p = make_product(authed, caliber_id=new["id"])
    assert authed.delete(f"/api/calibers/{new['id']}").status_code == 409  # in use
    authed.delete(f"/api/products/{p['id']}")
    assert authed.delete(f"/api/calibers/{new['id']}").status_code == 200


def test_deleting_product_returns_codes_to_unidentified(authed):
    p = make_product(authed)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "444444444444"})
    run_batch(authed, "in", [("444444444444", 2)])
    authed.delete(f"/api/products/{p['id']}")
    assert authed.get("/api/inventory/unidentified").json()[0]["boxes"] == 2


def test_code_cannot_be_stolen_by_second_product(authed):
    a, b = make_product(authed), make_product(authed, name="Other")
    authed.post(f"/api/products/{a['id']}/barcodes", json={"code": "555555555555"})
    assert authed.post(f"/api/products/{b['id']}/barcodes", json={"code": "555555555555"}).status_code == 409


def test_labels_allocate_and_render(authed):
    p = make_product(authed)
    r = authed.post("/api/labels/allocate", json={"count": 3, "product_id": p["id"]}).json()
    assert r == {"codes": ["QM000001"] * 3, "reused": False}   # one code for the product, printed 3 times
    assert authed.post("/api/labels/allocate", json={"count": 2}).json()["codes"] == ["QM000002", "QM000003"]
    for kind in ("code128", "qr"):
        r = authed.get(f"/api/labels/render?code=QM000001&type={kind}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and b"<svg" in r.content
    run_batch(authed, "in", [("qm000001", 2)])  # a hand scan of our own label resolves to the product
    assert authed.get("/api/inventory/items").json()["total_rounds"] == 100


def test_product_label_code_is_reused(authed):
    a = make_product(authed, name="A")
    b = make_product(authed, name="B")
    first = authed.post("/api/labels/allocate", json={"count": 1, "product_id": a["id"]}).json()
    again = authed.post("/api/labels/allocate", json={"count": 4, "product_id": a["id"]}).json()
    assert first == {"codes": ["QM000001"], "reused": False}
    assert again == {"codes": ["QM000001"] * 4, "reused": True}
    other = authed.post("/api/labels/allocate", json={"count": 1, "product_id": b["id"]}).json()
    assert other["codes"] == ["QM000002"] and other["reused"] is False
    # a product that only has a manufacturer UPC still gets its own QM label
    c = make_product(authed, name="C")
    authed.post(f"/api/products/{c['id']}/barcodes", json={"code": "012345678905"})
    got = authed.post("/api/labels/allocate", json={"count": 1, "product_id": c["id"]}).json()
    assert got["codes"] == ["QM000003"] and not got["reused"]


def test_csv_export(authed):
    p = make_product(authed)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "666666666666"})
    run_batch(authed, "in", [("666666666666", 2)])
    r = authed.get("/api/export/inventory.csv")
    assert r.status_code == 200 and "666666666666" in r.text and ",100" in r.text
    assert "in" in authed.get("/api/export/transactions.csv").text


def test_admin_can_add_stock_for_a_known_product(authed):
    p = make_product(authed)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "012345678905"})
    r = authed.post("/api/stock", json={"code": "012345678905", "boxes": 3, "note": "gun show"})
    assert r.status_code == 200, r.text
    rows = authed.get("/api/inventory/items").json()["products"]
    assert rows[0]["boxes"] == 3 and rows[0]["rounds"] == 150
    tx = authed.get("/api/transactions").json()[0]
    assert tx["kind"] == "in" and tx["boxes"] == 3 and tx["note"] == "gun show"


def test_add_stock_rejects_unknown_codes_and_bad_quantities(authed):
    assert authed.post("/api/stock", json={"code": "999999999999", "boxes": 1}).status_code == 404
    p = make_product(authed)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "012345678905"})
    assert authed.post("/api/stock", json={"code": "012345678905", "boxes": 0}).status_code == 422
    assert authed.post("/api/stock", json={"code": "012345678905", "boxes": -2}).status_code == 422


def test_scanned_code_is_located_in_the_drilldown(authed):
    p = make_product(authed, bullet_weight_gr=115)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "012345678905"})
    run_batch(authed, "in", [("012345678905", 3)])
    r = authed.get("/api/inventory/code/0012345678905").json()  # EAN-13 form of the same UPC
    assert r["found"] and r["identified"] and r["code"] == "012345678905"
    assert (r["boxes"], r["rounds"], r["code_boxes"]) == (3, 150, 3)
    assert r["caliber"] == str(p["caliber_id"]) and r["weight"] == "115" and r["row"] == str(p["id"])
    # the location it names really lists that product
    rows = authed.get("/api/inventory/drill", params={"caliber": r["caliber"], "weight": r["weight"]}).json()["rows"]
    assert [x["key"] for x in rows] == [r["row"]]


def test_locate_covers_no_stock_unidentified_and_unknown(authed):
    p = make_product(authed)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "012345678905"})
    run_batch(authed, "in", [("012345678905", 2), ("999999999999", 5)])
    run_batch(authed, "out", [("012345678905", 2)])
    assert authed.get("/api/inventory/code/012345678905").json()["boxes"] == 0
    u = authed.get("/api/inventory/code/999999999999").json()
    assert u["found"] and not u["identified"] and u["caliber"] == "unidentified" and u["row"] == "999999999999" and u["boxes"] == 5
    assert authed.get("/api/inventory/code/555555555555").json() == {"found": False, "code": "555555555555"}
    assert authed.get("/api/inventory/code/bad%20code").status_code == 400


def test_locate_needs_login(client):
    assert client.get("/api/inventory/code/012345678905").status_code == 401


def test_large_batch_quantities(authed):
    p = make_product(authed, rounds_per_box=1)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "012345678905"})
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    item = authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"}).json()["item"]
    url = f"/api/batches/{b['id']}/items/{item['id']}"
    assert authed.patch(url, json={"quantity": 99999}).status_code == 200
    assert authed.patch(url, json={"quantity": 100000}).status_code == 422
    assert authed.patch(url, json={"quantity": 12345}).status_code == 200
    authed.post(f"/api/batches/{b['id']}/finish")
    assert authed.get("/api/inventory/items").json()["total_rounds"] == 12345
