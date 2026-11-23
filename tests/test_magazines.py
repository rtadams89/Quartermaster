import csv
import io
import sqlite3
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from app import config
from app.codes import is_magazine_code
from tests.conftest import make_product

NS = {"pt": "http://schemas.brother.info/ptouch/2007/lbx/main", "text": "http://schemas.brother.info/ptouch/2007/lbx/text",
      "barcode": "http://schemas.brother.info/ptouch/2007/lbx/barcode", "style": "http://schemas.brother.info/ptouch/2007/lbx/style"}


def caliber_id(c):
    return c.get("/api/calibers").json()[0]["id"]


def new_mag(c, **over):
    body = {"firearm": "Glock 19", "caliber_id": caliber_id(c), "capacity": 15, "manufacturer": "Magpul", "model": "PMAG", **over}
    r = c.post("/api/magazines", json=body)
    assert r.status_code == 200, r.text
    return r.json()["magazines"]


def lbx(content):
    return ET.fromstring(zipfile.ZipFile(io.BytesIO(content)).read("label.xml"))


def test_requires_login(client):
    assert client.get("/api/magazines").status_code == 401
    assert client.post("/api/magazines/labels", json={"serials": ["M001"]}).status_code == 401


def test_each_magazine_is_its_own_record_with_a_short_unique_serial(authed):
    first = new_mag(authed)
    assert [m["serial"] for m in first] == ["M001"]
    several = new_mag(authed, firearm="AR-15", copies=3)
    assert [m["serial"] for m in several] == ["M002", "M003", "M004"]
    assert len(authed.get("/api/magazines").json()) == 4


def test_serials_are_never_reused_and_grow_a_digit_after_999(authed):
    new_mag(authed)
    assert authed.delete("/api/magazines/M001").status_code == 200
    assert new_mag(authed)[0]["serial"] == "M002"
    with __import__("app.db", fromlist=["SessionLocal"]).SessionLocal() as db:
        from app.models import Setting
        db.get(Setting, "magazine_serial").value = "998"
        db.commit()
    assert [m["serial"] for m in new_mag(authed, copies=3)] == ["M999", "M1000", "M1001"]


def test_magazine_codes_are_reserved_and_never_become_ammo_codes(authed):
    assert is_magazine_code("M001") and is_magazine_code("M1000") and not is_magazine_code("M01") and not is_magazine_code("QM000001")
    b = authed.post("/api/batches", json={"kind": "in"}).json()
    r = authed.post(f"/api/batches/{b['id']}/scan", json={"code": "m123"})
    assert r.status_code == 409 and "magazine" in r.json()["detail"]
    assert authed.get("/api/inventory/items").json()["unidentified"] == []
    p = make_product(authed)
    assert authed.post(f"/api/products/{p['id']}/barcodes", json={"code": "M001"}).status_code == 409


def test_list_is_sorted_filtered_by_firearm_and_suggests_names(authed):
    new_mag(authed, firearm="Zebra rifle", manufacturer="Colt")
    new_mag(authed, firearm="Alpha pistol")
    new_mag(authed, firearm="alpha  PISTOL", manufacturer="magpul")
    rows = authed.get("/api/magazines").json()
    assert [r["firearm"] for r in rows] == ["Alpha pistol", "Alpha pistol", "Zebra rifle"]
    assert [r["manufacturer"] for r in rows if r["firearm"] == "Alpha pistol"] == ["Magpul", "Magpul"]
    assert [r["serial"] for r in authed.get("/api/magazines?firearm=zebra RIFLE").json()] == ["M001"]
    assert authed.get("/api/magazines/suggestions").json() == {"firearms": ["Alpha pistol", "Zebra rifle"], "manufacturers": ["Colt", "Magpul"]}


def test_edit_changes_details_but_not_the_serial_and_reuses_spellings(authed):
    new_mag(authed, firearm="Glock 19")
    new_mag(authed, firearm="Sig P320")
    r = authed.patch("/api/magazines/m002", json={"firearm": "glock 19", "capacity": 17, "model": " Gen5 ", "notes": "spare"}).json()
    assert (r["serial"], r["firearm"], r["capacity"], r["model"], r["notes"]) == ("M002", "Glock 19", 17, "Gen5", "spare")
    only = authed.patch("/api/magazines/M001", json={"firearm": "glock 19"}).json()
    assert only["firearm"] == "Glock 19"
    lone = authed.patch("/api/magazines/M002", json={"firearm": "Sig p320"}).json()
    assert lone["firearm"] == "Sig p320"
    assert authed.patch("/api/magazines/M002", json={"firearm": "  "}).status_code == 422
    assert authed.patch("/api/magazines/M999", json={"capacity": 5}).status_code == 404


def test_delete_removes_one_magazine(authed):
    new_mag(authed, copies=2)
    assert authed.delete("/api/magazines/M001").status_code == 200
    assert [m["serial"] for m in authed.get("/api/magazines").json()] == ["M002"]
    assert authed.delete("/api/magazines/M001").status_code == 404


def test_bad_input_is_refused(authed):
    cid = caliber_id(authed)
    for body in ({"firearm": "", "caliber_id": cid, "capacity": 10}, {"firearm": "X", "caliber_id": cid, "capacity": 0},
                 {"firearm": "X", "caliber_id": cid, "capacity": 10, "copies": 101}):
        assert authed.post("/api/magazines", json=body).status_code == 422
    assert authed.post("/api/magazines", json={"firearm": "X", "caliber_id": 9999, "capacity": 10}).status_code == 400
    assert authed.post("/api/magazines", json={"firearm": "  ", "caliber_id": cid, "capacity": 10}).status_code == 422


def test_a_caliber_with_magazines_cannot_be_deleted(authed):
    m = new_mag(authed)[0]
    assert authed.delete(f"/api/calibers/{m['caliber_id']}").status_code == 409
    authed.delete("/api/magazines/M001")
    assert authed.delete(f"/api/calibers/{m['caliber_id']}").status_code == 200


def test_label_has_qr_of_the_serial_and_two_lines_in_one_format(authed):
    new_mag(authed, copies=2)
    root = lbx(authed.post("/api/magazines/labels", json={"serials": ["M001", "M002"]}).content)
    sheets = root.findall(".//style:sheet", NS)
    assert len(sheets) == 2
    for sheet, serial in zip(sheets, ("M001", "M002")):
        assert sheet.find(".//barcode:barcode/pt:data", NS).text == serial
        assert [t.find("pt:data", NS).text for t in sheet.findall(".//text:text", NS)] == ["Glock 19", serial]
        weights = {f.get("weight") for f in sheet.findall(".//text:logFont", NS)}
        assert weights == {"400"}


def test_one_file_per_label_names_files_by_serial_and_firearm(authed):
    new_mag(authed, copies=2)
    z = zipfile.ZipFile(io.BytesIO(authed.post("/api/magazines/labels", json={"serials": ["m001", "M002"], "mode": "many"}).content))
    assert sorted(z.namelist()) == ["M001 Glock 19.lbx", "M002 Glock 19.lbx"]
    single = authed.post("/api/magazines/labels", json={"serials": ["M001"], "mode": "many"})
    assert single.headers["content-disposition"].endswith('M001 Glock 19.lbx"')


def test_duplicate_serials_print_once_and_unknown_serials_are_refused(authed):
    new_mag(authed)
    root = lbx(authed.post("/api/magazines/labels", json={"serials": ["M001", "m001"]}).content)
    assert len(root.findall(".//style:sheet", NS)) == 1
    assert authed.post("/api/magazines/labels", json={"serials": ["M001", "M999"]}).status_code == 400


def test_csv_lists_each_serial_with_details(authed):
    new_mag(authed, firearm="=cmd", copies=2)
    rows = list(csv.DictReader(io.StringIO(authed.post("/api/magazines/labels.csv", json={"serials": ["M001", "M002"]}).text)))
    assert [r["Serial"] for r in rows] == ["M001", "M002"]
    assert rows[0]["Firearm"] == "'=cmd" and rows[0]["Capacity"] == "15" and rows[0]["Manufacturer"] == "Magpul" and rows[0]["Model"] == "PMAG"


def test_backup_roundtrip_keeps_magazines_and_old_backups_still_restore(authed):
    new_mag(authed, copies=2)
    backup = authed.get("/api/backup").content
    authed.delete("/api/magazines/M001")
    assert authed.post("/api/restore", content=backup).status_code == 200
    assert [m["serial"] for m in authed.get("/api/magazines").json()] == ["M001", "M002"]
    assert new_mag(authed)[0]["serial"] == "M003"

    old = Path(config.DB_PATH).parent / "old.db"
    old.write_bytes(backup)
    con = sqlite3.connect(old)
    con.execute("DROP TABLE magazine_items")
    con.commit()
    con.close()
    assert authed.post("/api/restore", content=old.read_bytes()).status_code == 200
    assert authed.get("/api/magazines").json() == []
