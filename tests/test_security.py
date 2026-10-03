from fastapi.testclient import TestClient

from app.csvsafe import safe, unsafe
from app.main import app

from .conftest import make_product


def test_security_headers_on_pages_and_api(authed):
    for path in ("/admin/", "/kiosk/", "/api/auth/status", "/"):
        h = authed.get(path).headers
        assert "script-src 'self'" in h["content-security-policy"], path
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"


def test_admin_page_has_no_inline_script(client):
    html = client.get("/admin/").text
    assert "<script>" not in html
    assert client.get("/admin/layout.js").status_code == 200


def test_cross_site_writes_are_refused(authed):
    r = authed.post("/api/calibers", json={"name": "X"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = authed.post("/api/calibers", json={"name": "X"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    # Same origin (what a browser sends from our own pages) still works.
    r = authed.post("/api/calibers", json={"name": "Same Origin"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_label_svg_is_sandboxed_even_for_hostile_codes(authed):
    r = authed.get("/api/labels/render", params={"code": "<SCRIPT>ALERT(1)</SCRIPT>", "type": "qr"})
    assert "sandbox" in r.headers["content-security-policy"]
    assert b"<script" not in r.content.lower()
    r = authed.get("/api/labels/render", params={"code": "<SCRIPT>ALERT(1)</SCRIPT>"})
    assert b"<script" not in r.content.lower()


def test_changing_the_pin_signs_out_other_browsers(authed):
    other = TestClient(app)
    assert other.post("/api/auth/login", json={"pin": "1234"}).status_code == 200
    assert other.get("/api/calibers").status_code == 200
    assert authed.post("/api/auth/change-pin", json={"current": "1234", "new": "5678"}).status_code == 200
    assert other.get("/api/calibers").status_code == 401
    assert authed.get("/api/calibers").status_code == 200


def test_csv_formula_cells_are_neutralised_and_round_trip(authed):
    assert safe("=1+1") == "'=1+1" and safe("-5") == "'-5" and safe(5) == 5 and safe("Federal") == "Federal"
    assert unsafe("'=1+1") == "=1+1" and unsafe("'quoted") == "'quoted"
    p = make_product(authed, name="=HYPERLINK(\"http://x\")", brand="@evil")
    exported = authed.get("/api/export/products.csv").text
    assert "'=HYPERLINK" in exported and "'@evil" in exported
    assert "\n=HYPERLINK" not in exported and ",=HYPERLINK" not in exported
    r = authed.post("/api/import/products?apply=true", content=exported.encode())
    assert r.status_code == 200, r.text
    again = authed.get(f"/api/products").json()
    assert [x["name"] for x in again if x["id"] == p["id"]] == ['=HYPERLINK("http://x")']


def test_oversized_csv_import_is_refused(authed):
    r = authed.post("/api/import/products", content=b"caliber,rounds_per_box\n" + b"9mm Luger,50\n" * 300000)
    assert r.status_code == 400


def test_lockout_settings_default_to_the_built_in_values(authed):
    got = authed.get("/api/settings").json()["lockout"]
    assert got == {"threshold": 5, "seconds": 60, "longest": 3600}


def test_lockout_settings_change_how_lockouts_behave(authed):
    r = authed.put("/api/settings/lockout", json={"threshold": 3, "seconds": 120})
    assert r.json() == {"threshold": 3, "seconds": 120, "longest": 3600}
    other = TestClient(app, client=("203.0.113.9", 1234))
    for _ in range(2):
        assert other.post("/api/auth/login", json={"pin": "0000"}).status_code == 401
    r = other.post("/api/auth/login", json={"pin": "0000"})
    assert r.status_code == 429 and 100 < int(r.headers["retry-after"]) <= 120


def test_lockout_settings_are_validated(authed):
    for bad in ({"threshold": 2, "seconds": 60}, {"threshold": 5, "seconds": 5}, {"threshold": 5, "seconds": 90000}):
        assert authed.put("/api/settings/lockout", json=bad).status_code == 422
    assert authed.put("/api/settings/lockout", json={"threshold": 5, "seconds": 7200}).json()["longest"] == 7200
