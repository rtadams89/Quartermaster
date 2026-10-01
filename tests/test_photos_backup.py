import io
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from app import config
from tests.conftest import make_product, run_batch


def image_bytes(size=(3000, 2000), fmt="PNG", color=(200, 30, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()


def dims(data: bytes):
    return Image.open(io.BytesIO(data)).size


def put_photo(c, code, data=None, **kw):
    return c.put(f"/api/barcodes/{code}/photo", content=data if data is not None else image_bytes(), **kw)


# ------------------------------------------------------------------- photos
def test_new_code_flag_only_true_on_first_ever_scan(authed):
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    first = authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"}).json()
    again = authed.post(f"/api/batches/{b['id']}/scan", json={"code": "012345678905"}).json()
    assert first["new_code"] is True and first["item"]["has_photo"] is False
    assert again["new_code"] is False
    authed.post(f"/api/batches/{b['id']}/finish")
    b2 = authed.post("/api/batches", json={"kind": "out"}).json()
    # Known from an earlier batch: still not "new", even though it has no photo.
    assert authed.post(f"/api/batches/{b2['id']}/scan", json={"code": "012345678905"}).json()["new_code"] is False


def test_photo_upload_resize_fetch_replace_delete(authed):
    run_batch(authed, "in", [("012345678905", 1)])
    assert authed.get("/api/barcodes/012345678905/photo").status_code == 404

    r = put_photo(authed, "012345678905")
    assert r.status_code == 200
    full = authed.get("/api/barcodes/012345678905/photo")
    thumb = authed.get("/api/barcodes/012345678905/photo?thumb=true")
    assert full.headers["content-type"] == "image/jpeg" and full.content[:2] == b"\xff\xd8"
    assert max(dims(full.content)) == 1280 and max(dims(thumb.content)) == 240
    assert dims(full.content) == (1280, 853)  # aspect ratio preserved

    # ETag revalidation
    etag = full.headers["etag"]
    assert authed.get("/api/barcodes/012345678905/photo", headers={"If-None-Match": etag}).status_code == 304

    # Replace: new bytes, new ETag
    put_photo(authed, "012345678905", image_bytes((400, 300), "JPEG", (10, 10, 200)))
    again = authed.get("/api/barcodes/012345678905/photo")
    assert again.headers["etag"] != etag and dims(again.content) == (400, 300)

    assert authed.delete("/api/barcodes/012345678905/photo").status_code == 200
    assert authed.get("/api/barcodes/012345678905/photo").status_code == 404
    assert authed.delete("/api/barcodes/012345678905/photo").status_code == 404


def test_photo_rejects_junk_and_unknown_code(authed):
    run_batch(authed, "in", [("012345678905", 1)])
    assert put_photo(authed, "012345678905", b"this is not an image").status_code == 400
    assert put_photo(authed, "012345678905", b"").status_code == 400
    assert put_photo(authed, "999999999999").status_code == 404
    assert authed.get("/api/barcodes/012345678905/photo").status_code == 404


def test_photo_strips_exif_and_handles_transparency(authed):
    run_batch(authed, "in", [("012345678905", 1)])
    img = Image.new("RGB", (200, 100), (1, 2, 3))
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    assert put_photo(authed, "012345678905", out.getvalue()).status_code == 200
    stored = authed.get("/api/barcodes/012345678905/photo").content
    assert b"SecretCameraMaker" not in stored
    rgba = io.BytesIO()
    Image.new("RGBA", (50, 50), (255, 0, 0, 0)).save(rgba, "PNG")
    assert put_photo(authed, "012345678905", rgba.getvalue()).status_code == 200


def test_photo_requires_login(client):
    assert client.get("/api/barcodes/012345678905/photo").status_code == 401
    assert client.put("/api/barcodes/012345678905/photo", content=image_bytes()).status_code == 401


def test_photo_flags_in_listings(authed):
    p = make_product(authed)
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "111111111111"})
    authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "222222222222"})
    run_batch(authed, "in", [("111111111111", 1), ("222222222222", 1), ("333333333333", 1)])
    put_photo(authed, "222222222222")
    put_photo(authed, "333333333333")

    prod = authed.get("/api/products").json()[0]
    assert prod["photo_codes"] == ["222222222222"]
    inv = authed.get("/api/inventory/items").json()
    assert inv["products"][0]["photo_code"] == "222222222222"
    assert [c["has_photo"] for c in inv["products"][0]["codes"]] == [False, True]
    assert inv["unidentified"][0]["has_photo"] is True
    assert authed.get("/api/inventory/unidentified").json()[0]["has_photo"] is True


def test_removing_an_unused_code_removes_its_photo(authed):
    authed.post("/api/batches", json={"kind": "in"})
    b = authed.get("/api/batches/current").json()
    authed.post(f"/api/batches/{b['id']}/scan", json={"code": "444444444444"})
    authed.post(f"/api/batches/{b['id']}/cancel")
    put_photo(authed, "444444444444")
    assert authed.delete("/api/barcodes/444444444444").status_code == 200
    assert authed.get("/api/barcodes/444444444444/photo").status_code == 404


def test_slash_not_allowed_in_codes(authed):
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    assert authed.post(f"/api/batches/{b['id']}/scan", json={"code": "AB/CD"}).status_code == 400


def test_photo_prompt_setting(authed):
    assert authed.get("/api/settings").json() == {"photo_prompt": True}
    authed.put("/api/settings", json={"photo_prompt": False})
    assert authed.get("/api/settings").json() == {"photo_prompt": False}


# ----------------------------------------------------------- backup/restore
def seed_data(c):
    p = make_product(c)
    c.post(f"/api/products/{p['id']}/barcodes", json={"code": "111111111111"})
    run_batch(c, "in", [("111111111111", 4), ("999999999999", 2)])
    put_photo(c, "111111111111", image_bytes((640, 480)))
    c.post("/api/calibers", json={"name": ".45 Colt"})
    c.post("/api/labels/allocate", json={"count": 3})
    c.put("/api/settings", json={"photo_prompt": False})
    return p


def snapshot_of(c):
    return {
        "inventory": c.get("/api/inventory/items").json(),
        "calibers": c.get("/api/calibers").json(),
        "history": [(t["code"], t["boxes"], t["kind"]) for t in c.get("/api/transactions").json()],
    }


def test_backup_is_a_clean_sqlite_file_without_secrets(authed, tmp_path):
    seed_data(authed)
    r = authed.get("/api/backup")
    assert r.status_code == 200
    assert "quartermaster-backup-" in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.db"')
    f = tmp_path / "b.db"
    f.write_bytes(r.content)
    con = sqlite3.connect(f)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM barcode_photos").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM settings WHERE key='pin_hash'").fetchone()[0] == 0
    assert con.execute("SELECT value FROM settings WHERE key='label_counter'").fetchone()[0] == "3"
    con.close()
    assert b"scrypt" not in r.content  # the PIN hash is not in there
    # no temp files left behind
    leftovers = [p.name for p in Path(config.DB_PATH).parent.glob("backup-*")]
    assert leftovers == []


def test_restore_roundtrip_replaces_data_but_keeps_pin_and_session(authed):
    seed_data(authed)
    want = snapshot_of(authed)
    backup = authed.get("/api/backup").content

    # Now change a lot, including the PIN.
    run_batch(authed, "out", [("111111111111", 3)])
    run_batch(authed, "in", [("555555555555", 9)])
    authed.delete("/api/barcodes/111111111111/photo")
    authed.post("/api/calibers", json={"name": "Should vanish"})
    authed.post("/api/auth/change-pin", json={"current": "1234", "new": "4321"})
    assert snapshot_of(authed) != want

    r = authed.post("/api/restore", content=backup)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"]["transactions"] == 2 and body["restored"]["barcode_photos"] == 1
    assert body["safety_copy"].startswith("backups/pre-restore-")

    assert snapshot_of(authed) == want                                    # data is exactly the backup's
    assert authed.get("/api/barcodes/111111111111/photo").status_code == 200  # photo came back
    assert authed.get("/api/settings").json() == {"photo_prompt": False}
    assert authed.get("/api/calibers").status_code == 200                 # still logged in
    authed.post("/api/auth/logout")
    assert authed.post("/api/auth/login", json={"pin": "4321"}).status_code == 200  # NEW pin kept
    assert authed.post("/api/auth/login", json={"pin": "1234"}).status_code == 401
    # label counter restored, so new labels don't collide with ones already printed
    assert authed.post("/api/labels/allocate", json={"count": 1}).json()["codes"] == ["QM000004"]


def test_restore_makes_a_safety_copy_of_what_it_replaces(authed):
    seed_data(authed)
    backup = authed.get("/api/backup").content
    run_batch(authed, "in", [("555555555555", 9)])
    name = authed.post("/api/restore", content=backup).json()["safety_copy"]
    copy = Path(config.DB_PATH).parent / name
    assert copy.exists()
    con = sqlite3.connect(copy)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3  # the pre-restore state
    con.close()


@pytest.mark.parametrize("junk", [b"not a database at all", b"", b"SQLite format 3\x00" + b"\x00" * 200])
def test_restore_rejects_junk_and_changes_nothing(authed, junk):
    seed_data(authed)
    want = snapshot_of(authed)
    assert authed.post("/api/restore", content=junk).status_code == 400
    assert snapshot_of(authed) == want


def test_restore_rejects_foreign_sqlite_file(authed, tmp_path):
    seed_data(authed)
    want = snapshot_of(authed)
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    con.commit()
    con.close()
    r = authed.post("/api/restore", content=other.read_bytes())
    assert r.status_code == 400 and "isn't a Quartermaster backup" in r.json()["detail"]
    assert snapshot_of(authed) == want


def test_restore_accepts_backup_from_before_photos_existed(authed, tmp_path):
    seed_data(authed)
    old = tmp_path / "old.db"
    old.write_bytes(authed.get("/api/backup").content)
    con = sqlite3.connect(old)
    con.execute("DROP TABLE barcode_photos")  # what a pre-photo backup looks like
    con.commit()
    con.close()
    r = authed.post("/api/restore", content=old.read_bytes())
    assert r.status_code == 200 and r.json()["restored"]["barcode_photos"] == 0
    assert authed.get("/api/barcodes/111111111111/photo").status_code == 404
    assert put_photo(authed, "111111111111").status_code == 200  # table exists again for new photos
    assert authed.get("/api/inventory/items").json()["total_boxes"] == 6


def test_restore_rejects_incompatible_schema(authed, tmp_path):
    seed_data(authed)
    bad = tmp_path / "bad.db"
    bad.write_bytes(authed.get("/api/backup").content)
    con = sqlite3.connect(bad)
    con.execute("ALTER TABLE products DROP COLUMN rounds_per_box")
    con.commit()
    con.close()
    r = authed.post("/api/restore", content=bad.read_bytes())
    assert r.status_code == 400 and "incompatible" in r.json()["detail"]


def test_backup_and_restore_need_login(client):
    assert client.get("/api/backup").status_code == 401
    assert client.post("/api/restore", content=b"x").status_code == 401


# -------------------------------------------------------------------- reset
def test_reset_returns_to_a_fresh_install_with_no_pin_and_no_copy(authed):
    backups = Path(config.DB_PATH).parent / "backups"
    before = sorted(backups.glob("*")) if backups.exists() else []
    make_product(authed)
    authed.put("/api/settings", json={"photo_prompt": False})
    run_batch(authed, "in", [("012345678905", 2)])
    r = authed.post("/api/reset", json={"pin": "1234", "confirm": "RESET"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}                                    # no safety copy is mentioned or made
    assert (sorted(backups.glob("*")) if backups.exists() else []) == before
    s = authed.get("/api/auth/status").json()
    assert s["pin_set"] is False and s["authenticated"] is False        # PIN gone, logged out
    assert authed.get("/api/calibers").status_code == 401
    # first-run setup works again, and the system is empty apart from the starter calibers
    assert authed.post("/api/auth/setup", json={"pin": "4321"}).status_code == 200
    assert authed.get("/api/products").json() == []
    assert authed.get("/api/transactions").json() == []
    assert len(authed.get("/api/calibers").json()) >= 15
    assert authed.get("/api/settings").json()["photo_prompt"] is True
    assert authed.post("/api/auth/logout").status_code == 200
    assert authed.post("/api/auth/login", json={"pin": "1234"}).status_code == 401  # the old PIN no longer works


def test_reset_needs_the_word_and_the_right_pin(authed):
    make_product(authed)
    assert authed.post("/api/reset", json={"pin": "1234", "confirm": "reset"}).status_code == 400
    assert authed.post("/api/reset", json={"pin": "9999", "confirm": "RESET"}).status_code == 400
    assert authed.post("/api/reset", json={"confirm": "RESET"}).status_code == 422
    assert authed.get("/api/products").json()  # nothing was erased


def test_reset_requires_login(client):
    assert client.post("/api/reset", json={"pin": "1234", "confirm": "RESET"}).status_code == 401
