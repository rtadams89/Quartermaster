import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="qm-test-")
os.environ["QM_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["QM_LOCKOUT_BASE_SECONDS"] = "60"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed(s)
    return SessionLocal


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def authed(client):
    """A client that has set up the PIN (1234) and is logged in."""
    assert client.post("/api/auth/setup", json={"pin": "1234"}).status_code == 200
    return client


def make_product(c, **over):
    cal = c.get("/api/calibers").json()[0]["id"]
    body = {"caliber_id": cal, "brand": "Federal", "name": "American Eagle", "bullet_weight_gr": 115,
            "bullet_type": "FMJ", "rounds_per_box": 50}
    body.update(over)
    r = c.post("/api/products", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def run_batch(c, kind, scans):
    """scans: list of (code, quantity). Starts a batch, scans, sets quantities, finishes."""
    b = c.post("/api/batches", json={"kind": kind}).json()
    for code, qty in scans:
        r = c.post(f"/api/batches/{b['id']}/scan", json={"code": code})
        assert r.status_code == 200, r.text
        if qty != 1:
            c.patch(f"/api/batches/{b['id']}/items/{r.json()['item']['id']}", json={"quantity": qty})
    return c.post(f"/api/batches/{b['id']}/finish")
