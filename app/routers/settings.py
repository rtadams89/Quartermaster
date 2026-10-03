"""Small server-side preferences that the kiosk and admin site share."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import security
from ..db import get_db
from ..models import Setting

router = APIRouter(prefix="/api/settings", dependencies=[Depends(security.require_auth)])

PHOTO_PROMPT = "kiosk_photo_prompt"


class SettingsIn(BaseModel):
    photo_prompt: bool


class LockoutIn(BaseModel):
    threshold: int = Field(ge=3, le=50)
    seconds: int = Field(ge=10, le=86400)


def photo_prompt_enabled(db: Session) -> bool:
    row = db.get(Setting, PHOTO_PROMPT)
    return row is None or row.value == "1"  # on by default


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    threshold, seconds, longest = security.lockout_policy(db)
    return {"photo_prompt": photo_prompt_enabled(db),
            "lockout": {"threshold": threshold, "seconds": seconds, "longest": longest}}


@router.put("/lockout")
def put_lockout(body: LockoutIn, db: Session = Depends(get_db)):
    """How many wrong PINs lock a device out, and for how long the first lockout lasts."""
    security.set_lockout_policy(db, body.threshold, body.seconds)
    threshold, seconds, longest = security.lockout_policy(db)
    return {"threshold": threshold, "seconds": seconds, "longest": longest}


@router.put("")
def put_settings(body: SettingsIn, db: Session = Depends(get_db)):
    row = db.get(Setting, PHOTO_PROMPT)
    if row is None:
        db.add(Setting(key=PHOTO_PROMPT, value="1" if body.photo_prompt else "0"))
    else:
        row.value = "1" if body.photo_prompt else "0"
    db.commit()
    return {"photo_prompt": body.photo_prompt}
