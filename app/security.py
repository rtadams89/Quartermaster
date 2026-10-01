"""PIN storage, login sessions with idle expiry, and per-IP lockout."""
import base64
import hashlib
import hmac
import math
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import config
from .db import get_db, utcnow
from .models import AuthSession, LoginAttempt, Setting

COOKIE = "qm_session"
PIN_KEY = "pin_hash"
_TOUCH_EVERY = timedelta(seconds=5)  # don't write last_seen on every request


# ---------------------------------------------------------------- PIN hashing
def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_pin_hash(pin: str, stored: str) -> bool:
    try:
        _, salt_b64, dig_b64 = stored.split("$")
        salt, want = base64.b64decode(salt_b64), base64.b64decode(dig_b64)
    except Exception:
        return False
    got = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(got, want)


def get_pin_hash(db: Session) -> str | None:
    row = db.get(Setting, PIN_KEY)
    return row.value if row else None


def set_pin(db: Session, pin: str) -> None:
    row = db.get(Setting, PIN_KEY)
    if row:
        row.value = hash_pin(pin)
    else:
        db.add(Setting(key=PIN_KEY, value=hash_pin(pin)))
    db.commit()


# ------------------------------------------------------------------ client IP
def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # The rightmost entry is the one our own proxy appended; anything to
            # its left was supplied by the client and can be forged.
            last = xff.split(",")[-1].strip()
            if last:
                return last
    return request.client.host if request.client else "unknown"


# -------------------------------------------------------------------- lockout
def lock_remaining(db: Session, ip: str) -> int:
    """Seconds this IP must still wait before trying a PIN (0 = may try)."""
    row = db.get(LoginAttempt, ip)
    if not row or not row.locked_until:
        return 0
    left = (row.locked_until - utcnow()).total_seconds()
    return math.ceil(left) if left > 0 else 0


def record_failure(db: Session, ip: str) -> int:
    """Count a bad PIN for this IP; return the new lock duration in seconds (0 if none)."""
    now = utcnow()
    row = db.get(LoginAttempt, ip)
    if row and row.last_failure_at and now - row.last_failure_at > timedelta(
        hours=config.LOCKOUT_FORGET_HOURS
    ):
        row.failures, row.locked_until = 0, None
    if not row:
        row = LoginAttempt(ip=ip, failures=0)
        db.add(row)
    row.failures += 1
    row.last_failure_at = now
    lock = 0
    if row.failures >= config.LOCKOUT_THRESHOLD:
        exp = row.failures - config.LOCKOUT_THRESHOLD
        lock = min(config.LOCKOUT_BASE_SECONDS * (2 ** min(exp, 20)), config.LOCKOUT_MAX_SECONDS)
        row.locked_until = now + timedelta(seconds=lock)
    db.commit()
    return lock


def record_success(db: Session, ip: str) -> None:
    row = db.get(LoginAttempt, ip)
    if row:
        db.delete(row)
        db.commit()


# ------------------------------------------------------------------- sessions
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: Session, response: Response, ip: str) -> None:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_hash_token(token), ip=ip))
    db.commit()
    purge_expired(db)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=config.COOKIE_SECURE,
        path="/",
    )


def purge_expired(db: Session) -> None:
    cutoff = utcnow() - timedelta(minutes=config.IDLE_MINUTES)
    db.execute(delete(AuthSession).where(AuthSession.last_seen < cutoff))
    db.commit()


def find_session(db: Session, request: Request, touch: bool = True) -> AuthSession | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash_token(token)))
    if not sess:
        return None
    now = utcnow()
    if now - sess.last_seen > timedelta(minutes=config.IDLE_MINUTES):
        db.delete(sess)
        db.commit()
        return None
    if touch and now - sess.last_seen > _TOUCH_EVERY:
        sess.last_seen = now
        db.commit()
    return sess


def end_session(db: Session, request: Request) -> None:
    sess = find_session(db, request, touch=False)
    if sess:
        db.delete(sess)
        db.commit()


def clear_sessions(db: Session) -> None:
    db.execute(delete(AuthSession))
    db.commit()


def require_auth(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    sess = find_session(db, request)
    if not sess:
        raise HTTPException(status_code=401, detail="PIN required")
    return sess
