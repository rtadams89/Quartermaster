import csv
import io
import zipfile
from xml.etree import ElementTree as ET

from app import ptouch
from tests.conftest import make_product, run_batch

NS = {"pt": "http://schemas.brother.info/ptouch/2007/lbx/main", "text": "http://schemas.brother.info/ptouch/2007/lbx/text",
      "barcode": "http://schemas.brother.info/ptouch/2007/lbx/barcode"}


def label_root(content: bytes):
    z = zipfile.ZipFile(io.BytesIO(content))
    assert sorted(z.namelist()) == ["label.xml", "prop.xml"]
    ET.fromstring(z.read("prop.xml"))
    return ET.fromstring(z.read("label.xml"))


def with_pack(c, qty=12, **over):
    p = make_product(c, **over)
    code = c.post("/api/labels/allocate", json={"count": 1, "product_id": p["id"], "pack_qty": qty}).json()["codes"][0]
    return p, code


def test_requires_login(client):
    for path in ("/api/labels/ptouch.csv", "/api/labels/ptouch.zip", "/api/labels/ptouch/QP000001.lbx"):
        assert client.get(path).status_code == 401


def test_pack_file_has_qr_and_caliber_product_total(authed):
    p, code = with_pack(authed)
    r = authed.get(f"/api/labels/ptouch/{code}.lbx")
    assert r.status_code == 200 and f'{code}.lbx' in r.headers["content-disposition"]
    root = label_root(r.content)
    qr = root.find(".//barcode:barcode", NS)
    assert qr.find("barcode:barcodeStyle", NS).get("protocol") == "QRCODE"
    assert qr.find("pt:data", NS).text == code
    style = qr.find("barcode:qrcodeStyle", NS)
    assert style.get("eccLevel") == "25%" and style.get("mbcs") == "1252" and style.get("version") == "auto"
    assert root.find(".//{http://schemas.brother.info/ptouch/2007/lbx/style}paper").get("autoLength") == "true"
    assert [t.find("pt:data", NS).text for t in root.findall(".//text:text", NS)] == [
        p["caliber"], "Federal American Eagle", "12 boxes"]


def test_by_the_round_pack_says_rounds_and_product_label_has_two_lines(authed):
    p, code = with_pack(authed, qty=100, rounds_per_box=1)
    texts = [t.find("pt:data", NS).text for t in label_root(authed.get(f"/api/labels/ptouch/{code}.lbx").content).findall(".//text:text", NS)]
    assert texts[-1] == "100 rounds"
    r = authed.post("/api/labels/allocate", json={"count": 1, "product_id": p["id"]}).json()["codes"][0]
    two = label_root(authed.get(f"/api/labels/ptouch/{r}.lbx").content).findall(".//text:text", NS)
    assert len(two) == 2


def test_qr_fits_the_printable_height_of_12mm_tape(authed):
    for code in ("QP000001", "QM000123", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"):
        q = ptouch.qr_layout(code)
        assert q["side"] <= ptouch.PRINT_PT


def test_unknown_code_is_404_and_bad_code_is_400(authed):
    assert authed.get("/api/labels/ptouch/QP999999.lbx").status_code == 404
    assert authed.get("/api/labels/ptouch/%20.lbx").status_code in (400, 404)


def test_csv_lists_packs_and_product_labels_with_details(authed):
    p, pack = with_pack(authed, qty=6)
    label = authed.post("/api/labels/allocate", json={"count": 1, "product_id": p["id"]}).json()["codes"][0]
    rows = list(csv.DictReader(io.StringIO(authed.get("/api/labels/ptouch.csv").text)))
    assert list(rows[0]) == ptouch.COLUMNS
    by_code = {r["Code"]: r for r in rows}
    assert by_code[pack]["Type"] == "Pack" and by_code[pack]["Total"] == "6 boxes"
    assert by_code[label]["Type"] == "Product" and by_code[label]["Total"] == ""
    assert by_code[pack]["Product"] == "Federal American Eagle" and "115 gr FMJ" in by_code[pack]["Details"]
    assert by_code[pack]["Caliber"] == p["caliber"] and "·" not in by_code[pack]["Details"]


def test_zip_has_one_file_per_label_named_for_the_label(authed):
    p, pack = with_pack(authed)
    run_batch(authed, "in", [(pack, 1)])
    z = zipfile.ZipFile(io.BytesIO(authed.get("/api/labels/ptouch.zip").content))
    names = z.namelist()
    assert len(names) == 2 and all(n.endswith(".lbx") for n in names)
    assert any(n.startswith(pack) and "12 boxes" in n for n in names)
    for n in names:
        label_root(z.read(n))
