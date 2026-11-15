import csv
import io
import zipfile
from xml.etree import ElementTree as ET

from app import ptouch
from tests.conftest import make_product

NS = {"pt": "http://schemas.brother.info/ptouch/2007/lbx/main", "text": "http://schemas.brother.info/ptouch/2007/lbx/text",
      "barcode": "http://schemas.brother.info/ptouch/2007/lbx/barcode", "style": "http://schemas.brother.info/ptouch/2007/lbx/style"}


def label_root(content: bytes):
    z = zipfile.ZipFile(io.BytesIO(content))
    assert sorted(z.namelist()) == ["label.xml", "prop.xml"]
    ET.fromstring(z.read("prop.xml"))
    return ET.fromstring(z.read("label.xml"))


def post(c, path, items, mode="one"):
    return c.post(f"/api/labels/{path}", json={"items": items, "mode": mode})


def page_texts(sheet):
    return [t.find("pt:data", NS).text for t in sheet.findall(".//text:text", NS)]


def test_requires_login(client):
    for path in ("resolve", "ptouch", "ptouch.csv"):
        assert client.post(f"/api/labels/{path}", json={"items": [{}]}).status_code == 401


def test_pack_page_has_qr_and_caliber_product_total(authed):
    p = make_product(authed)
    r = post(authed, "ptouch", [{"product_id": p["id"], "pack_qty": 12}])
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('quartermaster-labels.lbx"')
    root = label_root(r.content)
    qr = root.find(".//barcode:barcode", NS)
    assert qr.find("barcode:barcodeStyle", NS).get("protocol") == "QRCODE"
    assert qr.find("pt:data", NS).text.startswith("QP")
    style = qr.find("barcode:qrcodeStyle", NS)
    assert style.get("eccLevel") == "25%" and style.get("mbcs") == "1252" and style.get("version") == "auto"
    assert root.find(".//style:paper", NS).get("autoLength") == "true"
    assert page_texts(root) == [p["caliber"], "Federal American Eagle", "12 boxes"]


def test_by_the_round_pack_says_rounds_and_product_label_has_two_lines(authed):
    p = make_product(authed, rounds_per_box=1)
    pack = label_root(post(authed, "ptouch", [{"product_id": p["id"], "pack_qty": 100}]).content)
    assert page_texts(pack)[-1] == "100 rounds"
    assert len(page_texts(label_root(post(authed, "ptouch", [{"product_id": p["id"]}]).content))) == 2


def test_blank_label_is_just_a_qr_code(authed):
    root = label_root(post(authed, "ptouch", [{}]).content)
    assert root.find(".//barcode:barcode/pt:data", NS).text == "QM000001" and page_texts(root) == []


def test_one_file_has_a_page_per_label_and_copy(authed):
    a = make_product(authed, name="A")
    b = make_product(authed, name="B")
    root = label_root(post(authed, "ptouch", [{"product_id": a["id"], "copies": 2}, {"product_id": b["id"], "pack_qty": 6}]).content)
    sheets = root.findall(".//style:sheet", NS)
    assert [s.get("name") for s in sheets] == ["Sheet 1", "Sheet 2", "Sheet 3"]
    assert [page_texts(s)[1] for s in sheets] == ["Federal A", "Federal A", "Federal B"]


def test_many_files_has_one_per_unique_label_with_copies_in_the_name(authed):
    a = make_product(authed, name="A")
    b = make_product(authed, name="B")
    r = post(authed, "ptouch", [{"product_id": a["id"], "copies": 3}, {"product_id": b["id"], "pack_qty": 12}, {"product_id": a["id"]}], mode="many")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(z.namelist())
    assert len(names) == 2 and all(n.endswith(".lbx") for n in names)
    assert any(n.startswith("QM000001") and n.endswith(" x4.lbx") for n in names)
    assert any(n.startswith("QP") and "12 boxes" in n for n in names)
    for n in names:
        assert len(label_root(z.read(n)).findall(".//style:sheet", NS)) == 1


def test_many_files_with_a_single_label_downloads_the_lbx_itself(authed):
    p = make_product(authed)
    r = post(authed, "ptouch", [{"product_id": p["id"]}], mode="many")
    assert r.headers["content-disposition"].endswith('.lbx"') and len(label_root(r.content).findall(".//style:sheet", NS)) == 1


def test_csv_has_a_row_per_copy_with_details(authed):
    p = make_product(authed)
    r = post(authed, "ptouch.csv", [{"product_id": p["id"], "pack_qty": 6, "copies": 2}, {"product_id": p["id"]}, {}])
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0]) == ptouch.COLUMNS and len(rows) == 4
    pack, product, blank = rows[0], rows[2], rows[3]
    assert pack == rows[1] and pack["Type"] == "Pack" and pack["Total"] == "6 boxes"
    assert product["Type"] == "Product" and product["Total"] == ""
    assert blank["Type"] == "Blank" and blank["Product"] == ""
    assert pack["Product"] == "Federal American Eagle" and "115 gr FMJ" in pack["Details"]
    assert pack["Caliber"] == p["caliber"] and "·" not in pack["Details"]


def test_existing_codes_can_be_added_by_code(authed):
    p = make_product(authed)
    pack = authed.post("/api/labels/resolve", json={"items": [{"product_id": p["id"], "pack_qty": 12}]}).json()["rows"][0]["code"]
    rows = authed.post("/api/labels/resolve", json={"items": [{"code": pack.lower(), "copies": 2}]}).json()["rows"]
    assert [(r["code"], r["type"], r["total"], r["copies"]) for r in rows] == [(pack, "Pack", "12 boxes", 2)]
    assert authed.post("/api/labels/resolve", json={"items": [{"code": "QM999999"}]}).status_code == 400
    assert authed.post("/api/labels/resolve", json={"items": [{"code": "bad code!"}]}).status_code == 400


def test_a_failed_job_creates_no_codes(authed):
    p = make_product(authed)
    r = authed.post("/api/labels/resolve", json={"items": [{"product_id": p["id"], "pack_qty": 12}, {"code": "QM999999"}]})
    assert r.status_code == 400
    assert authed.post("/api/labels/resolve", json={"items": [{"product_id": p["id"], "pack_qty": 12}]}).json()["rows"][0]["code"] == "QP000001"


def test_qr_fits_the_height_of_12mm_tape(authed):
    for code in ("QP000001", "QM000123"):
        q = ptouch.qr_layout(code)
        assert q["side"] + ptouch.QR_Y_PT <= ptouch.TAPE_PT
