"""Small server-side preferences that the kiosk and admin site share."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import security
from ..db import get_db
from ..models import Setting

router = APIRouter(prefix="/api/settings", dependencies=[Depends(security.require_auth)])

PHOTO_PROMPT = "kiosk_photo_prompt"
SOUND_ON = "kiosk_sound"
SOUND_VOLUME = "kiosk_volume"


class SettingsIn(BaseModel):
    photo_prompt: bool


class SoundIn(BaseModel):
    enabled: bool
    volume: int = Field(ge=0, le=100)


def sound_settings(db: Session) -> dict:
    on, vol = db.get(Setting, SOUND_ON), db.get(Setting, SOUND_VOLUME)
    try:
        volume = int(vol.value) if vol else 50
    except ValueError:
        volume = 50
    return {"enabled": on is None or on.value == "1", "volume": volume}


def _put(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


class LockoutIn(BaseModel):
    threshold: int = Field(ge=3, le=50)
    seconds: int = Field(ge=10, le=86400)


def photo_prompt_enabled(db: Session) -> bool:
    row = db.get(Setting, PHOTO_PROMPT)
    return row is None or row.value == "1"  # on by default


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    threshold, seconds, longest = security.lockout_policy(db)
    return {"photo_prompt": photo_prompt_enabled(db), "sound": sound_settings(db),
            "lockout": {"threshold": threshold, "seconds": seconds, "longest": longest}}


@router.put("/sound")
def put_sound(body: SoundIn, db: Session = Depends(get_db)):
    """The kiosk's beeps: on or off, and how loud (0 to 100)."""
    _put(db, SOUND_ON, "1" if body.enabled else "0")
    _put(db, SOUND_VOLUME, str(body.volume))
    db.commit()
    return sound_settings(db)


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
