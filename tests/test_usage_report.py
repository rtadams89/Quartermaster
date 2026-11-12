from datetime import datetime

from app.db import SessionLocal, utcnow
from app.models import Transaction
from tests.conftest import make_product, run_batch


def stocked(c, qty=10, **over):
    p = make_product(c, **over)
    code = f"0{p['id']:011d}"
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, qty)])
    return p, code


def retime(*stamps):
    with SessionLocal() as db:
        for t, ts in zip(db.query(Transaction).order_by(Transaction.id).all(), stamps):
            t.ts = ts
        db.commit()


def test_requires_login(client):
    assert client.get("/api/reports/onhand?year=2020").status_code == 401


def test_month_view_has_every_day_and_tracks_changes(authed):
    p, code = stocked(authed)
    run_batch(authed, "out", [(code, 2)])
    retime(datetime(2020, 3, 5, 12), datetime(2020, 3, 10, 12))
    r = authed.get("/api/reports/onhand?year=2020&month=3").json()
    v = r["series"][0]["values"]
    assert len(r["labels"]) == 31 and len(v) == 31
    assert v[3] == 0 and v[4] == 500 and v[8] == 500 and v[9] == 400 and v[30] == 400
    assert r["total"][9] == 400 and r["now"] == 400


def test_year_view_is_month_end_values(authed):
    p, code = stocked(authed)
    run_batch(authed, "out", [(code, 2)])
    retime(datetime(2020, 3, 5, 12), datetime(2020, 5, 10, 12))
    r = authed.get("/api/reports/onhand?year=2020").json()
    assert r["labels"][0] == "Jan" and len(r["labels"]) == 12
    assert r["series"][0]["values"] == [0, 0, 500, 500, 400, 400, 400, 400, 400, 400, 400, 400]


def test_future_points_are_blank(authed):
    stocked(authed)
    r = authed.get(f"/api/reports/onhand?year={utcnow().year + 1}").json()
    assert r["series"][0]["values"] == [None] * 12 and r["total"] == [None] * 12


def test_time_zone_moves_the_day(authed):
    stocked(authed)
    retime(datetime(2020, 4, 1, 3, 0))
    path = "/api/reports/onhand?year=2020&month=3&tz="
    utc = authed.get(path + "UTC").json()["series"][0]["values"]
    la = authed.get(path + "America/Los_Angeles").json()["series"][0]["values"]
    assert utc[30] == 0 and la[29] == 0 and la[30] == 500
    bad = authed.get(path + "Nope/Zone").json()["series"][0]["values"]
    assert bad[30] == 0


def test_by_product_when_caliber_given(authed):
    a, _ = stocked(authed, name="Alpha")
    stocked(authed, name="Beta", rounds_per_box=1, qty=300)
    r = authed.get(f"/api/reports/onhand?year={utcnow().year}&caliber_id={a['caliber_id']}").json()
    assert r["by"] == "product"
    assert sorted(s["now"] for s in r["series"]) == [300, 500] and r["now"] == 800


def test_current_month_shows_the_value_so_far(authed):
    stocked(authed)
    now = utcnow()
    r = authed.get(f"/api/reports/onhand?year={now.year}").json()
    assert r["series"][0]["values"][now.month - 1] == 500
    assert r["series"][0]["values"][now.month:] == [None] * (12 - now.month)
