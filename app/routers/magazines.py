"""Magazines: each one is its own record with a unique serial, and its label."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import ptouch, security
from ..db import get_db, iso
from ..models import BatchMagazine, Caliber, Magazine, MagazineEvent, Setting
from ..services import magazine_dict, magazine_states
from .labels import csv_download, files_download, lbx_download

router = APIRouter(prefix="/api/magazines", dependencies=[Depends(security.require_auth)])

COUNTER_KEY = "magazine_serial"
CSV_COLUMNS = ["Serial", "Firearm", "Caliber", "Capacity", "Manufacturer", "Model"]


class MagazineIn(BaseModel):
    firearm: str = Field(min_length=1, max_length=80)
    caliber_id: int
    capacity: int = Field(ge=1, le=999)
    manufacturer: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=2000)
    copies: int = Field(default=1, ge=1, le=100)


class MagazinePatch(BaseModel):
    firearm: str | None = Field(default=None, min_length=1, max_length=80)
    caliber_id: int | None = None
    capacity: int | None = Field(default=None, ge=1, le=999)
    manufacturer: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)


class LabelsIn(BaseModel):
    serials: list[str] = Field(min_length=1, max_length=500)
    mode: str = Field(default="one", pattern="^(one|many)$")


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _spelling(db: Session, column, value: str, skip_serial: str | None = None) -> str:
    """The spelling other magazines already use for this firearm or manufacturer ("glock 19" becomes "Glock 19")."""
    value = _tidy(value)
    query = select(column).where(func.lower(column) == value.lower(), column != "")
    if skip_serial:
        query = query.where(Magazine.serial != skip_serial)
    return db.scalar(query.group_by(column).order_by(func.count().desc(), column)) or value


def _new_serials(db: Session, count: int) -> list[str]:
    row = db.get(Setting, COUNTER_KEY)
    n = int(row.value) if row else 0
    serials = [f"M{n + i + 1:03d}" for i in range(count)]
    if row:
        row.value = str(n + count)
    else:
        db.add(Setting(key=COUNTER_KEY, value=str(count)))
    return serials


def _check_caliber(db: Session, caliber_id: int) -> None:
    if not db.get(Caliber, caliber_id):
        raise HTTPException(400, "Unknown caliber")


@router.get("")
def list_magazines(firearm: str | None = None, db: Session = Depends(get_db)):
    query = select(Magazine)
    if firearm:
        query = query.where(func.lower(Magazine.firearm) == firearm.lower())
    states = magazine_states(db)
    rows = [magazine_dict(m, states) for m in db.scalars(query)]
    return sorted(rows, key=lambda r: (r["firearm"].lower(), r["caliber"].lower(), r["capacity"], r["manufacturer"].lower(), r["model"].lower(), r["serial"]))


@router.get("/history")
def history(serial: str | None = None, limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)):
    query = select(MagazineEvent, Magazine).join(Magazine, Magazine.serial == MagazineEvent.serial).order_by(MagazineEvent.id.desc()).limit(limit)
    if serial:
        query = query.where(MagazineEvent.serial == serial.strip().upper())
    return [{"serial": e.serial, "firearm": m.firearm, "model": m.model, "kind": e.kind, "ts": iso(e.ts)} for e, m in db.execute(query)]


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db)):
    def distinct(column):
        return sorted(db.scalars(select(column).where(column != "").distinct()), key=str.lower)
    return {"firearms": distinct(Magazine.firearm), "manufacturers": distinct(Magazine.manufacturer)}


@router.post("")
def create_magazines(body: MagazineIn, db: Session = Depends(get_db)):
    _check_caliber(db, body.caliber_id)
    firearm = _spelling(db, Magazine.firearm, body.firearm)
    if not firearm:
        raise HTTPException(422, "Firearm is required")
    made = [Magazine(serial=s, firearm=firearm, caliber_id=body.caliber_id, capacity=body.capacity,
                     manufacturer=_spelling(db, Magazine.manufacturer, body.manufacturer), model=_tidy(body.model), notes=body.notes)
            for s in _new_serials(db, body.copies)]
    db.add_all(made)
    db.commit()
    states = magazine_states(db)
    return {"magazines": [magazine_dict(m, states) for m in made]}


@router.patch("/{serial}")
def update_magazine(serial: str, body: MagazinePatch, db: Session = Depends(get_db)):
    m = db.get(Magazine, serial.upper())
    if not m:
        raise HTTPException(404, "Magazine not found")
    if body.caliber_id is not None:
        _check_caliber(db, body.caliber_id)
        m.caliber_id = body.caliber_id
    if body.firearm is not None:
        if not _tidy(body.firearm):
            raise HTTPException(422, "Firearm is required")
        m.firearm = _spelling(db, Magazine.firearm, body.firearm, m.serial)
    if body.capacity is not None:
        m.capacity = body.capacity
    if body.manufacturer is not None:
        m.manufacturer = _spelling(db, Magazine.manufacturer, body.manufacturer, m.serial)
    if body.model is not None:
        m.model = _tidy(body.model)
    if body.notes is not None:
        m.notes = body.notes
    db.commit()
    return magazine_dict(m, magazine_states(db))


@router.delete("/{serial}")
def delete_magazine(serial: str, db: Session = Depends(get_db)):
    m = db.get(Magazine, serial.upper())
    if not m:
        raise HTTPException(404, "Magazine not found")
    for row in (*db.scalars(select(MagazineEvent).where(MagazineEvent.serial == m.serial)),
                *db.scalars(select(BatchMagazine).where(BatchMagazine.serial == m.serial))):
        db.delete(row)
    db.flush()
    db.delete(m)
    db.commit()
    return {"ok": True}


def _label_rows(db: Session, serials: list[str]) -> list[dict]:
    rows = []
    for serial in dict.fromkeys(s.strip().upper() for s in serials):
        m = db.get(Magazine, serial)
        if not m:
            raise HTTPException(400, f"No magazine with the serial {serial}")
        rows.append({"code": serial, "lines": [m.firearm, serial], "plain": True, "magazine": m})
    return rows


@router.post("/labels")
def labels_file(body: LabelsIn, db: Session = Depends(get_db)):
    rows = _label_rows(db, body.serials)
    if body.mode == "one":
        return lbx_download(rows)
    return files_download({f"{ptouch.file_stem(r)}.lbx": ptouch.lbx_bytes([r]) for r in rows})


@router.post("/labels.csv")
def labels_csv(body: LabelsIn, db: Session = Depends(get_db)):
    return csv_download(CSV_COLUMNS, [[r["code"], r["magazine"].firearm, r["magazine"].caliber.name, r["magazine"].capacity,
                                       r["magazine"].manufacturer, r["magazine"].model] for r in _label_rows(db, body.serials)])
