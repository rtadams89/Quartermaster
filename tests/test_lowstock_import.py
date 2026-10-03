import io

import pytest

from tests.conftest import make_product, run_batch


def cal_id(c, name):
    return next(x["id"] for x in c.get("/api/calibers").json() if x["name"] == name)


def add_code(c, p, code):
    assert c.post(f"/api/products/{p['id']}/barcodes", json={"code": code}).status_code == 200


# ------------------------------------------------------------------ low stock
def test_caliber_and_product_levels_report_when_below(authed):
    p = make_product(authed, rounds_per_box=50)           # 9mm Luger (first caliber)
    add_code(authed, p, "012345678905")
    run_batch(authed, "in", [("012345678905", 4)])        # 200 rounds
    assert authed.get("/api/low-stock").json()["count"] == 0
    cid = cal_id(authed, "9mm Luger")
    authed.patch(f"/api/calibers/{cid}", json={"min_rounds": 500})
    body = {k: p[k] for k in ("caliber_id", "brand", "name", "bullet_weight_gr", "bullet_type", "rounds_per_box")}
    authed.put(f"/api/products/{p['id']}", json={**body, "min_rounds": 150})
    low = authed.get("/api/low-stock").json()
    assert low["count"] == 1 and low["calibers"][0]["name"] == "9mm Luger"
    assert low["calibers"][0]["rounds"] == 200 and low["calibers"][0]["min_rounds"] == 500
    assert low["products"] == []                          # 200 is not below the product's 150
    run_batch(authed, "out", [("012345678905", 2)])       # 100 rounds
    low = authed.get("/api/low-stock").json()
    assert low["count"] == 2 and low["products"][0]["rounds"] == 100


def test_exactly_at_the_level_is_not_low_and_clearing_removes_it(authed):
    p = make_product(authed)
    add_code(authed, p, "012345678905")
    run_batch(authed, "in", [("012345678905", 2)])        # 100 rounds
    cid = cal_id(authed, "9mm Luger")
    authed.patch(f"/api/calibers/{cid}", json={"min_rounds": 100})
    assert authed.get("/api/low-stock").json()["count"] == 0
    authed.patch(f"/api/calibers/{cid}", json={"min_rounds": 101})
    assert authed.get("/api/low-stock").json()["count"] == 1
    authed.patch(f"/api/calibers/{cid}", json={"min_rounds": None})
    assert authed.get("/api/low-stock").json()["count"] == 0
    assert next(c for c in authed.get("/api/calibers").json() if c["id"] == cid)["min_rounds"] is None
    authed.patch(f"/api/calibers/{cid}", json={"active": True})  # unrelated patch leaves the level alone
    authed.patch(f"/api/calibers/{cid}", json={"min_rounds": 300})
    authed.patch(f"/api/calibers/{cid}", json={"active": True})
    assert next(c for c in authed.get("/api/calibers").json() if c["id"] == cid)["min_rounds"] == 300


def test_a_caliber_with_a_level_stays_in_the_kiosk_list_when_it_runs_out(authed):
    cid = cal_id(authed, "9mm Luger")
    authed.patch(f"/api/calibers/{cid}", json={"min_rounds": 200})
    rows = authed.get("/api/inventory/drill").json()["rows"]
    row = next(r for r in rows if r["label"] == "9mm Luger")
    assert row["low"] is True and row["rounds"] == 0 and row["drillable"] is False
    p = make_product(authed)
    add_code(authed, p, "012345678905")
    run_batch(authed, "in", [("012345678905", 1)])
    row = next(r for r in authed.get("/api/inventory/drill").json()["rows"] if r["label"] == "9mm Luger")
    assert row["low"] is True and row["drillable"] is True       # 50 rounds, still under 200
    run_batch(authed, "in", [("012345678905", 4)])
    row = next(r for r in authed.get("/api/inventory/drill").json()["rows"] if r["label"] == "9mm Luger")
    assert row["low"] is False


def test_product_level_shows_on_the_product_rows_and_goes_with_the_product(authed):
    p = make_product(authed, bullet_weight_gr=115)
    add_code(authed, p, "012345678905")
    body = {k: p[k] for k in ("caliber_id", "brand", "name", "bullet_weight_gr", "bullet_type", "rounds_per_box")}
    authed.put(f"/api/products/{p['id']}", json={**body, "min_rounds": 500})
    run_batch(authed, "in", [("012345678905", 2)])
    rows = authed.get("/api/inventory/drill", params={"caliber": str(p["caliber_id"]), "weight": "115"}).json()["rows"]
    assert rows[0]["low"] is True
    assert authed.get("/api/products").json()[0]["min_rounds"] == 500
    authed.delete(f"/api/products/{p['id']}")
    assert authed.get("/api/low-stock").json()["count"] == 0


def test_low_stock_needs_login(client):
    assert client.get("/api/low-stock").status_code == 401


# ---------------------------------------------------------------- import / export
HEAD = "caliber,brand,name,weight_gr,type,rounds_per_box,cost_per_box,low_stock_rounds,codes,notes\n"


def do_import(c, text, apply=False):
    return c.post(f"/api/import/products?apply={'true' if apply else 'false'}", content=text.encode())


def test_export_then_import_is_a_no_op_round_trip(authed):
    p = make_product(authed, bullet_weight_gr=115.5, cost_per_box=19.99, notes="range ammo, \"bulk\"")
    add_code(authed, p, "012345678905")
    add_code(authed, p, "036000291452")
    csv_text = authed.get("/api/export/products.csv").text
    assert csv_text.splitlines()[0] == HEAD.strip()
    r = do_import(authed, csv_text).json()
    assert (r["create"], r["update"], r["unchanged"], r["error_count"]) == (0, 0, 1, 0)


def test_import_creates_updates_and_identifies_codes(authed):
    # a code that was scanned but never described becomes identified, keeping its history
    run_batch(authed, "in", [("012345678905", 3)])
    text = HEAD + (
        "9mm Luger,Federal,American Eagle,115,FMJ,50,18.50,200,012345678905,\n"
        "Brand New Cal,Acme,Thunder,,,20,,,555555555555 666666666666,note\n"
    )
    pre = do_import(authed, text).json()
    assert (pre["create"], pre["update"], pre["new_calibers"], pre["applied"]) == (2, 0, ["Brand New Cal"], False)
    assert authed.get("/api/products").json() == []                  # a preview changes nothing
    done = do_import(authed, text, apply=True).json()
    assert done["applied"] is True
    prods = {p["label"]: p for p in authed.get("/api/products").json()}
    assert prods["Federal American Eagle"]["codes"] == ["012345678905"]
    assert prods["Federal American Eagle"]["min_rounds"] == 200
    assert prods["Acme Thunder"]["codes"] == ["555555555555", "666666666666"]
    assert authed.get("/api/inventory/items").json()["products"][0]["boxes"] == 3  # history picked up the details
    # second run with an edited cost updates, not duplicates
    text2 = text.replace("18.50", "21.00")
    r = do_import(authed, text2, apply=True).json()
    assert (r["create"], r["update"], r["unchanged"]) == (0, 1, 1)
    assert len(authed.get("/api/products").json()) == 2
    assert {p["cost_per_box"] for p in authed.get("/api/products").json()} == {21.0, None}


def test_columns_left_out_of_the_file_are_left_alone(authed):
    p = make_product(authed, notes="keep me", cost_per_box=9)
    add_code(authed, p, "012345678905")
    text = "caliber,rounds_per_box,codes\n9mm Luger,50,012345678905\n"
    r = do_import(authed, text, apply=True).json()
    assert r["update"] == 0 and r["unchanged"] == 1
    got = authed.get("/api/products").json()[0]
    assert got["notes"] == "keep me" and got["cost_per_box"] == 9


def test_problem_rows_are_reported_and_nothing_is_applied(authed):
    text = HEAD + (
        "9mm Luger,A,One,115,FMJ,50,,,012345678905,\n"
        "9mm Luger,B,Two,abc,FMJ,50,,,,\n"
        ",C,Three,,,50,,,,\n"
        "9mm Luger,D,Four,,,0,,,,\n"
        "9mm Luger,E,Five,,,50,,,caf\u00e9,\n"
        "9mm Luger,F,Six,,,50,,,012345678905,\n"
    )
    pre = do_import(authed, text).json()
    assert pre["error_count"] == 5 and {e["row"] for e in pre["errors"]} == {3, 4, 5, 6, 7}
    r = do_import(authed, text, apply=True)
    assert r.status_code == 400 and "nothing was imported" in r.json()["detail"]
    assert authed.get("/api/products").json() == []


def test_barcode_that_belongs_to_another_product_is_refused(authed):
    p = make_product(authed)
    add_code(authed, p, "012345678905")
    text = HEAD + "9mm Luger,Other,Thing,,,25,,,012345678905 036000291452,\n"
    r = do_import(authed, text).json()          # matches the owning product through the code, so it is an update
    assert r["error_count"] == 0 and r["update"] == 1
    q = make_product(authed, name="Second")
    add_code(authed, q, "036000291452")
    r = do_import(authed, HEAD + "9mm Luger,X,Y,,,25,,,012345678905 036000291452,\n").json()
    assert r["error_count"] == 1 and "different products" in r["errors"][0]["error"]


def test_file_must_have_the_required_columns_and_be_small(authed):
    assert do_import(authed, "brand,name\nA,B\n").status_code == 400
    assert authed.post("/api/import/products", content=b"").status_code == 400
    assert authed.post("/api/import/products", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 400


def test_import_handles_excel_style_files(authed):
    text = "﻿Caliber,Rounds_Per_Box,Extra\r\n9mm Luger,50,ignored\r\n"
    r = do_import(authed, text, apply=True).json()
    assert r["create"] == 1 and r["ignored_columns"] == ["extra"]
    legacy = "caliber,brand,rounds_per_box\n9mm Luger,Caf\xe9,50\n".encode("cp1252")
    assert authed.post("/api/import/products?apply=true", content=legacy).json()["create"] == 1


def test_duplicate_new_product_rows_are_refused(authed):
    text = HEAD + "9mm Luger,A,One,,,50,,,,\n9mm Luger,A,One,,,50,,,,\n"
    r = do_import(authed, text).json()
    assert r["error_count"] == 1 and r["errors"][0]["row"] == 3


def test_import_export_need_login(client):
    assert client.get("/api/export/products.csv").status_code == 401
    assert client.post("/api/import/products", content=b"x").status_code == 401


def test_costs_accept_dollar_signs_and_export_with_two_decimals(authed):
    text = HEAD + "9mm Luger,A,One,,,50,\"$1,234.5\",,,\n9mm Luger,B,Two,,,50,18,,,\n9mm Luger,C,Three,,,50,,,,\n"
    assert do_import(authed, text, apply=True).json()["create"] == 3
    costs = {p["name"]: p["cost_per_box"] for p in authed.get("/api/products").json()}
    assert costs == {"One": 1234.5, "Two": 18.0, "Three": None}
    exported = authed.get("/api/export/products.csv").text
    assert ",1234.50," in exported and ",18.00," in exported
    bad = do_import(authed, HEAD + "9mm Luger,D,Four,,,50,about ten,,,\n").json()
    assert bad["error_count"] == 1 and "cost_per_box" in bad["errors"][0]["error"]
