import sqlite3
from pathlib import Path

from app import config
from app.db import SessionLocal
from app.models import Barcode, PackCode
from tests.conftest import make_product, run_batch


def stocked(c, qty=10, **over):
    p = make_product(c, **over)
    code = f"0{p['id']:011d}"
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": code})
    run_batch(c, "in", [(code, qty)])
    return p, code


def pack(c, p, qty):
    r = c.post("/api/labels/allocate", json={"count": 1, "product_id": p["id"], "pack_qty": qty})
    assert r.status_code == 200, r.text
    return r.json()


def test_allocate_pack_reuses_same_product_and_quantity(authed):
    p, _ = stocked(authed)
    a = pack(authed, p, 12)
    b = pack(authed, p, 12)
    c = pack(authed, p, 6)
    assert a["codes"][0].startswith("QP") and not a["reused"]
    assert b["codes"] == a["codes"] and b["reused"]
    assert c["codes"] != a["codes"]
    many = authed.post("/api/labels/allocate", json={"count": 3, "product_id": p["id"], "pack_qty": 12}).json()
    assert many["codes"] == a["codes"] * 3


def test_pack_needs_a_product_and_a_sensible_size(authed):
    p, _ = stocked(authed)
    assert authed.post("/api/labels/allocate", json={"count": 1, "pack_qty": 12}).status_code == 400
    assert authed.post("/api/labels/allocate", json={"count": 1, "product_id": p["id"], "pack_qty": 1}).status_code == 422
    assert authed.post("/api/labels/allocate", json={"count": 1, "product_id": 9999, "pack_qty": 5}).status_code == 400


def test_scanning_a_pack_adds_its_quantity_to_the_product(authed):
    p, code = stocked(authed, qty=100)
    pc = pack(authed, p, 12)["codes"][0]
    b = authed.post("/api/batches", json={"kind": "out"}).json()
    r = authed.post(f"/api/batches/{b['id']}/scan", json={"code": pc}).json()
    assert r["pack"] == 12 and r["known"] and not r["new_code"]
    assert r["item"]["code"] == code and r["item"]["quantity"] == 12 and r["item"]["on_hand"] == 100
    r = authed.post(f"/api/batches/{b['id']}/scan", json={"code": pc}).json()
    assert r["item"]["quantity"] == 24
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": code})
    assert authed.get("/api/batches/current").json()["items"][0]["quantity"] == 25
    authed.post(f"/api/batches/{b['id']}/finish")
    assert authed.get("/api/inventory/code/" + code).json()["boxes"] == 75


def test_pack_code_never_becomes_a_barcode_or_stock_row(authed):
    p, code = stocked(authed)
    pc = pack(authed, p, 5)["codes"][0]
    run_batch(authed, "in", [(pc, 1)])
    with SessionLocal() as db:
        assert db.get(Barcode, pc) is None and db.get(PackCode, pc)
    prod = next(x for x in authed.get("/api/products").json() if x["id"] == p["id"])
    assert pc not in prod["codes"]
    assert authed.get("/api/inventory/code/" + pc).json()["found"]


def test_pack_for_a_product_with_no_other_code_gets_a_label(authed):
    p = make_product(authed)
    pc = pack(authed, p, 20)["codes"][0]
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    item = authed.post(f"/api/batches/{b['id']}/scan", json={"code": pc}).json()["item"]
    assert item["code"].startswith("QM") and item["quantity"] == 20


def test_by_the_round_pack_counts_rounds(authed):
    p, code = stocked(authed, qty=500, rounds_per_box=1)
    pc = pack(authed, p, 100)["codes"][0]
    b = authed.post("/api/batches", json={"kind": "out"}).json()
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": pc})
    res = authed.post(f"/api/batches/{b['id']}/finish").json()
    assert res["rounds"] == 100 and res["boxes"] == 0


def test_list_and_delete_packs(authed):
    p, _ = stocked(authed)
    pc = pack(authed, p, 12)["codes"][0]
    rows = authed.get("/api/labels/packs").json()
    assert [(r["code"], r["quantity"], r["product"]["id"]) for r in rows] == [(pc, 12, p["id"])]
    assert authed.delete(f"/api/labels/packs/{pc}").status_code == 200
    assert authed.get("/api/labels/packs").json() == []
    assert authed.delete(f"/api/labels/packs/{pc}").status_code == 404


def test_deleting_a_product_removes_its_packs(authed):
    p, _ = stocked(authed)
    pack(authed, p, 12)
    assert authed.delete(f"/api/products/{p['id']}").status_code == 200
    assert authed.get("/api/labels/packs").json() == []


def test_backup_roundtrip_keeps_packs_and_old_backups_still_restore(authed):
    p, _ = stocked(authed)
    pc = pack(authed, p, 12)["codes"][0]
    backup = authed.get("/api/backup").content
    authed.delete(f"/api/labels/packs/{pc}")
    assert authed.post("/api/restore", content=backup).status_code == 200
    assert [r["code"] for r in authed.get("/api/labels/packs").json()] == [pc]

    old = Path(config.DB_PATH).parent / "old.db"
    old.write_bytes(backup)
    con = sqlite3.connect(old)
    con.execute("DROP TABLE pack_codes")
    con.commit()
    con.close()
    assert authed.post("/api/restore", content=old.read_bytes()).status_code == 200
    assert authed.get("/api/labels/packs").json() == []


def test_a_pack_code_cannot_be_added_as_a_product_barcode(authed):
    p, _ = stocked(authed)
    pc = pack(authed, p, 12)["codes"][0]
    other = make_product(authed, name="Other")
    assert authed.post(f"/api/products/{other['id']}/barcodes", json={"code": pc}).status_code == 409
