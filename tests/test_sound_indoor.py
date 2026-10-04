import sqlite3

from tests.conftest import make_product, run_batch
from tests.test_lowstock_import import add_code, do_import


# ------------------------------------------------------------------ indoor range safe
def test_products_default_to_indoor_safe(authed):
    p = make_product(authed)
    assert p["indoor_safe"] is True


def test_can_mark_and_unmark_outdoor_only(authed):
    p = make_product(authed, indoor_safe=False)
    assert p["indoor_safe"] is False
    assert "outdoor range only" in p["spec"]
    body = {k: p[k] for k in ("caliber_id", "brand", "name", "bullet_weight_gr", "bullet_type", "rounds_per_box")}
    r = authed.put(f"/api/products/{p['id']}", json={**body, "indoor_safe": True})
    assert r.status_code == 200 and r.json()["indoor_safe"] is True


def test_batch_items_carry_the_flag_for_the_kiosk(authed):
    p = make_product(authed, indoor_safe=False)
    add_code(authed, p, "012345678905")
    b = authed.post("/api/batches", json={"kind": "out"}).json()
    r = authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"}).json()
    assert r["item"]["product"]["indoor_safe"] is False


def test_csv_round_trip_keeps_the_flag(authed):
    p = make_product(authed, indoor_safe=False)
    add_code(authed, p, "012345678905")
    text = authed.get("/api/export/products.csv").text
    assert text.splitlines()[1].split(",")[-1] == "no"
    r = do_import(authed, text).json()
    assert (r["update"], r["unchanged"], r["error_count"]) == (0, 1, 0)
    edited = text.replace(",no\r\n", ",yes\r\n").replace(",no\n", ",yes\n")
    assert do_import(authed, edited).json()["update"] == 1
    do_import(authed, edited, apply=True)
    assert authed.get("/api/products").json()[0]["indoor_safe"] is True


def test_csv_rejects_a_bad_indoor_value(authed):
    p = make_product(authed)
    add_code(authed, p, "012345678905")
    text = authed.get("/api/export/products.csv").text.replace(",yes", ",maybe")
    assert do_import(authed, text).json()["error_count"] == 1


def test_older_csv_without_the_column_leaves_it_alone(authed):
    p = make_product(authed, indoor_safe=False)
    add_code(authed, p, "012345678905")
    head = "caliber,manufacturer,name,weight_gr,type,rounds_per_box,cost_per_box,low_stock_rounds,codes,notes\n"
    rows = authed.get("/api/export/products.csv").text.splitlines()[1:]
    old = head + "\n".join(r.rsplit(",", 1)[0] for r in rows) + "\n"
    do_import(authed, old, apply=True)
    assert authed.get("/api/products").json()[0]["indoor_safe"] is False


# ------------------------------------------------------------------ older databases
def _strip_column(raw: bytes, tmp_path) -> bytes:
    """Rebuild a database file as an older version would have made it (no indoor_safe)."""
    src = tmp_path / "full.db"
    src.write_bytes(raw)
    con = sqlite3.connect(src)
    con.execute("ALTER TABLE products DROP COLUMN indoor_safe")
    con.commit()
    con.close()
    return src.read_bytes()


def test_restore_accepts_a_backup_made_before_the_column_existed(authed, tmp_path):
    p = make_product(authed, name="Old")
    add_code(authed, p, "012345678905")
    old = _strip_column(authed.get("/api/backup").content, tmp_path)
    make_product(authed, name="Newer", indoor_safe=False)
    r = authed.post("/api/restore", content=old)
    assert r.status_code == 200, r.text
    prods = authed.get("/api/products").json()
    assert [x["name"] for x in prods] == ["Old"] and prods[0]["indoor_safe"] is True


def test_startup_upgrade_adds_the_column(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    import app.db as db

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as con:
        con.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        con.execute(text("INSERT INTO products (id, name) VALUES (1, 'x')"))
    monkeypatch.setattr(db, "engine", eng)
    db.upgrade_schema()
    db.upgrade_schema()  # harmless the second time
    with eng.begin() as con:
        assert con.execute(text("SELECT indoor_safe FROM products")).scalar() == 1


# ------------------------------------------------------------------ sounds
def test_sound_defaults_to_on_at_half_volume(authed):
    assert authed.get("/api/settings").json()["sound"] == {"enabled": True, "volume": 50}


def test_sound_can_be_changed(authed):
    r = authed.put("/api/settings/sound", json={"enabled": False, "volume": 20})
    assert r.status_code == 200
    assert authed.get("/api/settings").json()["sound"] == {"enabled": False, "volume": 20}


def test_sound_volume_is_validated(authed):
    for v in (-1, 101):
        assert authed.put("/api/settings/sound", json={"enabled": True, "volume": v}).status_code == 422
