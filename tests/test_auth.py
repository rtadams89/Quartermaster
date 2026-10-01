from datetime import timedelta

from app import config
from app.db import utcnow
from app.models import AuthSession


def test_requires_pin_setup_then_login(client):
    s = client.get("/api/auth/status").json()
    assert s == {"pin_set": False, "authenticated": False, "idle_minutes": 15, "retry_after": 0}
    assert client.get("/api/calibers").status_code == 401
    assert client.post("/api/auth/login", json={"pin": "1234"}).status_code == 409
    assert client.post("/api/auth/setup", json={"pin": "1234"}).status_code == 200
    assert client.get("/api/calibers").status_code == 200
    # setup can't be used again to take over
    assert client.post("/api/auth/setup", json={"pin": "9999"}).status_code == 409


def test_login_logout_roundtrip(authed):
    authed.post("/api/auth/logout")
    assert authed.get("/api/calibers").status_code == 401
    assert authed.post("/api/auth/login", json={"pin": "0000"}).status_code == 401
    assert authed.post("/api/auth/login", json={"pin": "1234"}).status_code == 200
    assert authed.get("/api/calibers").status_code == 200


def test_pin_must_be_four_digits(client):
    assert client.post("/api/auth/setup", json={"pin": "12345"}).status_code == 422
    assert client.post("/api/auth/setup", json={"pin": "12a4"}).status_code == 422


def test_session_expires_after_15_idle_minutes(authed, db):
    assert authed.get("/api/calibers").status_code == 200
    with db() as s:
        for row in s.query(AuthSession):
            row.last_seen = utcnow() - timedelta(minutes=config.IDLE_MINUTES + 1)
        s.commit()
    assert authed.get("/api/calibers").status_code == 401


def test_activity_keeps_session_alive(authed, db):
    with db() as s:
        for row in s.query(AuthSession):
            row.last_seen = utcnow() - timedelta(minutes=config.IDLE_MINUTES - 1)
        s.commit()
    assert authed.post("/api/auth/ping").status_code == 200  # refreshes last_seen
    with db() as s:
        for row in s.query(AuthSession):
            assert utcnow() - row.last_seen < timedelta(minutes=1)


def _fail(client, ip, n=1, pin="0000"):
    last = None
    for _ in range(n):
        last = client.post("/api/auth/login", json={"pin": pin}, headers={"X-Forwarded-For": ip})
    return last


def test_lockout_is_per_source_ip(client, monkeypatch):
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    client.post("/api/auth/setup", json={"pin": "1234"})
    client.post("/api/auth/logout")

    # Four failures: still just 401s.
    r = _fail(client, "10.0.0.5", 4)
    assert r.status_code == 401
    # Fifth failure trips the lock.
    r = _fail(client, "10.0.0.5")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) == 60
    # Even the correct PIN is refused from that IP while locked...
    r = _fail(client, "10.0.0.5", pin="1234")
    assert r.status_code == 429
    # ...but a different IP is unaffected.
    r = _fail(client, "10.0.0.9", pin="1234")
    assert r.status_code == 200


def test_lockout_escalates_and_clears_on_success(client, monkeypatch, db):
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    client.post("/api/auth/setup", json={"pin": "1234"})
    client.post("/api/auth/logout")
    _fail(client, "10.0.0.5", 5)  # 60 s lock
    from app.models import LoginAttempt

    with db() as s:  # pretend the lock has expired
        row = s.get(LoginAttempt, "10.0.0.5")
        row.locked_until = utcnow() - timedelta(seconds=1)
        s.commit()
    r = _fail(client, "10.0.0.5")  # sixth failure -> 120 s
    assert r.status_code == 429 and int(r.headers["Retry-After"]) == 120
    with db() as s:
        row = s.get(LoginAttempt, "10.0.0.5")
        row.locked_until = utcnow() - timedelta(seconds=1)
        s.commit()
    assert _fail(client, "10.0.0.5", pin="1234").status_code == 200
    with db() as s:
        assert s.get(LoginAttempt, "10.0.0.5") is None


def test_forwarded_header_ignored_unless_trusted(client):
    client.post("/api/auth/setup", json={"pin": "1234"})
    client.post("/api/auth/logout")
    # Spoofing a new IP on every attempt must not dodge the lockout.
    for i in range(5):
        r = client.post("/api/auth/login", json={"pin": "0000"}, headers={"X-Forwarded-For": f"1.2.3.{i}"})
    assert r.status_code == 429


def test_change_pin(authed):
    assert authed.post("/api/auth/change-pin", json={"current": "0000", "new": "4321"}).status_code == 400
    assert authed.post("/api/auth/change-pin", json={"current": "1234", "new": "4321"}).status_code == 200
    authed.post("/api/auth/logout")
    assert authed.post("/api/auth/login", json={"pin": "1234"}).status_code == 401
    assert authed.post("/api/auth/login", json={"pin": "4321"}).status_code == 200


def test_admin_can_list_and_clear_lockouts(authed, monkeypatch):
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    authed.post("/api/auth/logout")
    _fail(authed, "10.0.0.5", 5)
    authed.post("/api/auth/login", json={"pin": "1234"}, headers={"X-Forwarded-For": "10.0.0.9"})
    rows = authed.get("/api/security/lockouts").json()
    assert rows[0]["ip"] == "10.0.0.5" and rows[0]["locked"] is True
    authed.post("/api/security/lockouts/clear", json={})
    assert authed.get("/api/security/lockouts").json() == []
