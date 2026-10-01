"""First-run data: a starter caliber list (fully editable from the admin site)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Caliber

DEFAULT_CALIBERS = [
    "9mm Luger", ".45 ACP", ".40 S&W", ".380 ACP", "10mm Auto", ".357 Magnum", ".38 Special",
    ".44 Magnum", ".22 LR", ".22 WMR", ".17 HMR", ".223 Rem / 5.56 NATO", ".300 AAC Blackout",
    "7.62x39", ".308 Win / 7.62x51", "6.5 Creedmoor", ".30-06", "12 gauge", "20 gauge",
]


def seed(db: Session) -> None:
    if db.scalar(select(func.count(Caliber.id))):
        return
    for i, name in enumerate(DEFAULT_CALIBERS, start=1):
        db.add(Caliber(name=name, sort_order=i * 10))
    db.commit()
