from datetime import timedelta

from app.db import SessionLocal, utcnow
from app.models import Transaction
from tests.conftest import make_product, run_batch


def stocked(c, **over):
    p = make_product(c, **over)
    code = f"0{p['id']:011d}"
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, 10)])
    return p, code


def test_lists_recent_checkouts_newest_first(authed):
    a, ca = stocked(authed, name="First")
    b, cb = stocked(authed, name="Second", rounds_per_box=1)
    run_batch(authed, "out", [(ca, 3)])
    run_batch(authed, "out", [(cb, 4)])
    rows = authed.get("/api/batches/recent-out").json()
    assert [(r["code"], r["quantity"]) for r in rows] == [(cb, 4), (ca, 3)]
    assert rows[1]["product"]["spec"] and rows[0]["product"]["rounds_per_box"] == 1
    assert rows[0]["ts"].endswith("Z")


def test_only_checkouts_from_the_last_seven_days(authed):
    _, code = stocked(authed)
    run_batch(authed, "out", [(code, 2)])
    run_batch(authed, "out", [(code, 5)])
    authed.post("/api/adjustments", json={"code": code, "boxes": -1})
    with SessionLocal() as s:
        old = s.query(Transaction).filter(Transaction.kind == "out", Transaction.boxes == -2).one()
        old.ts = utcnow() - timedelta(days=8)
        s.commit()
    assert [r["quantity"] for r in authed.get("/api/batches/recent-out").json()] == [5]
    assert [r["quantity"] for r in authed.get("/api/batches/recent-out?days=10").json()] == [5, 2]


def test_unidentified_checkout_is_listed_by_code(authed):
    run_batch(authed, "out", [("099999999990", 2)])
    row = authed.get("/api/batches/recent-out").json()[0]
    assert row["code"] == "099999999990" and row["product"] is None and row["quantity"] == 2


def test_checking_a_checkout_back_in_uses_its_quantity(authed):
    _, code = stocked(authed)
    run_batch(authed, "out", [(code, 6)])
    row = authed.get("/api/batches/recent-out").json()[0]
    bid = authed.post("/api/batches", json={"kind": "in"}).json()["id"]
    r = authed.post(f"/api/batches/{bid}/scan", json={"code": row["code"], "quantity": row["quantity"]}).json()
    assert r["item"]["quantity"] == 6
    authed.post(f"/api/batches/{bid}/finish")
    assert authed.get("/api/inventory/drill").json()["rows"][0]["boxes"] == 10


def test_requires_login(client):
    assert client.get("/api/batches/recent-out").status_code == 401
