from datetime import datetime

from app.db import SessionLocal, utcnow
from app.models import Barcode, Transaction
from tests.conftest import make_product, run_batch


def stocked(c, qty=10, **over):
    p = make_product(c, **over)
    code = f"0{p['id']:011d}"
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, qty)])
    return p, code


def move_all(ts):
    with SessionLocal() as db:
        for t in db.query(Transaction).all():
            t.ts = ts
        db.commit()


def test_requires_login(client):
    assert client.get("/api/reports/usage").status_code == 401


def test_counts_checked_out_rounds_by_month_and_cost(authed):
    p, code = stocked(authed, cost_per_box=25)
    run_batch(authed, "out", [(code, 3)])
    now = utcnow()
    r = authed.get(f"/api/reports/usage?year={now.year}").json()
    assert r["by"] == "caliber" and len(r["rows"]) == 1
    row = r["rows"][0]
    assert row["out_total"] == 150 and row["in_total"] == 500
    assert row["out"][now.month - 1] == 150
    assert row["cost_total"] == 75.0 and row["onhand_now"] == 350
    assert r["total"]["out_total"] == 150


def test_past_month_bucketing_and_future_months(authed):
    p, code = stocked(authed)
    run_batch(authed, "out", [(code, 2)])
    move_all(datetime(2020, 3, 15, 12))
    r = authed.get("/api/reports/usage?year=2020").json()
    row = r["rows"][0]
    assert row["out"][2] == 100 and sum(row["out"]) == 100
    assert row["onhand"][1] == 0 and row["onhand"][2] == 400 and row["onhand"][11] == 400
    assert not any(c["future"] for c in r["columns"])
    assert 2020 in r["years"]


def test_all_years_has_a_column_per_year(authed):
    p, code = stocked(authed)
    run_batch(authed, "out", [(code, 1)])
    move_all(datetime(2020, 3, 15, 12))
    r = authed.get("/api/reports/usage").json()
    assert [c["label"] for c in r["columns"]][0] == "2020"
    assert r["rows"][0]["out"][0] == 50


def test_time_zone_moves_month_boundary(authed):
    p, code = stocked(authed)
    run_batch(authed, "out", [(code, 1)])
    move_all(datetime(2020, 4, 1, 3, 0))
    utc = authed.get("/api/reports/usage?year=2020&tz=UTC").json()["rows"][0]["out"]
    la = authed.get("/api/reports/usage?year=2020&tz=America/Los_Angeles").json()["rows"][0]["out"]
    assert utc[3] == 50 and la[2] == 50
    bad = authed.get("/api/reports/usage?year=2020&tz=Nope/Zone").json()["rows"][0]["out"]
    assert bad[3] == 50


def test_by_product_when_caliber_given(authed):
    a, ca = stocked(authed, name="Alpha")
    b, cb = stocked(authed, name="Beta", rounds_per_box=1, qty=500)
    run_batch(authed, "out", [(ca, 1), (cb, 20)])
    r = authed.get(f"/api/reports/usage?year={utcnow().year}&caliber_id={a['caliber_id']}").json()
    assert r["by"] == "product"
    assert sorted(x["out_total"] for x in r["rows"]) == [20, 50]
    assert r["total"]["out_total"] == 70


def test_unpriced_and_unidentified_are_reported(authed):
    p, code = stocked(authed)
    run_batch(authed, "out", [(code, 1)])
    with SessionLocal() as db:
        db.add(Barcode(code="MYSTERY"))
        db.flush()
        db.add(Transaction(ts=utcnow(), code="MYSTERY", boxes=-2, kind="out"))
        db.commit()
    r = authed.get(f"/api/reports/usage?year={utcnow().year}").json()
    assert r["unpriced"] == 1 and r["unidentified_boxes"] == 2
    assert r["rows"][0]["out_total"] == 50
