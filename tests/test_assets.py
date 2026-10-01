import os

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from app.assets import BUILD, FreshStatic


def test_scripts_carry_the_server_build_and_pages_can_see_it(client):
    js = client.get("/shared/api.js")
    assert js.status_code == 200
    assert f"BUILD = '{BUILD}'" in js.text and "__BUILD__'" not in js.text.split("BUILD = ")[1].split("\n")[0]
    assert client.get("/api/health").json()["build"] == BUILD
    assert client.get("/api/auth/status").json()["build"] == BUILD


def test_static_files_revalidate_with_content_etags(client):
    r = client.get("/kiosk/kiosk.js")
    assert r.headers["cache-control"] == "no-cache" and "last-modified" not in r.headers
    etag = r.headers["etag"]
    again = client.get("/kiosk/kiosk.js", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.headers["etag"] == etag
    assert client.get("/kiosk/kiosk.js", headers={"If-None-Match": '"stale"'}).status_code == 200
    assert client.get("/kiosk/").status_code == 200            # index.html is served too
    assert client.get("/favicon.ico").status_code == 200


def test_changed_content_is_never_called_unchanged_even_if_mtime_and_size_match(tmp_path):
    f = tmp_path / "a.js"
    f.write_text("const x = 'one';")
    stamp = os.stat(f).st_mtime_ns
    c = TestClient(Starlette(routes=[Mount("/s", FreshStatic(directory=tmp_path))]))
    first = c.get("/s/a.js").headers["etag"]
    f.write_text("const x = 'two';")                       # same length
    os.utime(f, ns=(stamp, stamp))                        # same modification time
    r = c.get("/s/a.js", headers={"If-None-Match": first})
    assert r.status_code == 200 and "two" in r.text and r.headers["etag"] != first
