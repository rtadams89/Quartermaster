from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__, config, security
from ..assets import BUILD
from ..db import get_db, iso, utcnow
from ..models import LoginAttempt

router = APIRouter(prefix="/api")

PIN = Field(pattern=r"^\d{4}$")


class PinIn(BaseModel):
    pin: str = PIN


class ChangePinIn(BaseModel):
    current: str = PIN
    new: str = PIN


class ClearIn(BaseModel):
    ip: str | None = None  # omit to clear every IP


def _too_many(seconds: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=f"Too many attempts. Try again in {seconds} s.",
        headers={"Retry-After": str(seconds)},
    )


@router.get("/health")
def health():
    return {"ok": True, "version": __version__, "build": BUILD}


@router.get("/auth/status")
def status(request: Request, db: Session = Depends(get_db)):
    ip = security.client_ip(request)
    return {
        "pin_set": security.get_pin_hash(db) is not None,
        "authenticated": security.find_session(db, request) is not None,
        "idle_minutes": config.IDLE_MINUTES,
        "version": __version__,
        "build": BUILD,
        "retry_after": security.lock_remaining(db, ip),
    }


@router.post("/auth/setup")
def setup(body: PinIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """First-run only: choose the PIN. Logs the caller in."""
    if security.get_pin_hash(db) is not None:
        raise HTTPException(status_code=409, detail="PIN already set")
    security.set_pin(db, body.pin)
    security.start_session(db, response, security.client_ip(request))
    return {"ok": True}


@router.post("/auth/login")
def login(body: PinIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = security.client_ip(request)
    stored = security.get_pin_hash(db)
    if stored is None:
        raise HTTPException(status_code=409, detail="PIN not set up yet")
    wait = security.lock_remaining(db, ip)
    if wait:
        raise _too_many(wait)
    if not security.verify_pin_hash(body.pin, stored):
        lock = security.record_failure(db, ip)
        if lock:
            raise _too_many(lock)
        row = db.get(LoginAttempt, ip)
        left = max(0, config.LOCKOUT_THRESHOLD - (row.failures if row else 0))
        raise HTTPException(status_code=401, detail=f"Wrong PIN. {left} attempt(s) left before lockout.")
    security.record_success(db, ip)
    security.start_session(db, response, ip)
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    security.end_session(db, request)
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}


@router.post("/auth/ping", dependencies=[Depends(security.require_auth)])
def ping():
    """Called by the UIs while the user is active, to keep the session alive."""
    return {"ok": True}


@router.post("/auth/change-pin", dependencies=[Depends(security.require_auth)])
def change_pin(body: ChangePinIn, request: Request, db: Session = Depends(get_db)):
    ip = security.client_ip(request)
    wait = security.lock_remaining(db, ip)
    if wait:
        raise _too_many(wait)
    stored = security.get_pin_hash(db)
    if not stored or not security.verify_pin_hash(body.current, stored):
        lock = security.record_failure(db, ip)
        if lock:
            raise _too_many(lock)
        raise HTTPException(status_code=400, detail="Current PIN is incorrect")
    security.record_success(db, ip)
    security.set_pin(db, body.new)
    return {"ok": True}


@router.get("/security/lockouts", dependencies=[Depends(security.require_auth)])
def lockouts(db: Session = Depends(get_db)):
    now = utcnow()
    rows = db.scalars(select(LoginAttempt).order_by(LoginAttempt.last_failure_at.desc()))
    return [
        {
            "ip": r.ip,
            "failures": r.failures,
            "locked": bool(r.locked_until and r.locked_until > now),
            "locked_until": iso(r.locked_until),
            "last_failure_at": iso(r.last_failure_at),
        }
        for r in rows
    ]


@router.post("/security/lockouts/clear", dependencies=[Depends(security.require_auth)])
def clear_lockouts(body: ClearIn, db: Session = Depends(get_db)):
    q = select(LoginAttempt)
    if body.ip:
        q = q.where(LoginAttempt.ip == body.ip)
    for r in db.scalars(q):
        db.delete(r)
    db.commit()
    return {"ok": True}
