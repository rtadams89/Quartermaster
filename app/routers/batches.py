"""Kiosk check in / check out: scans are queued in a draft batch, then committed."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import security
from ..codes import is_magazine_code, normalize_code
from ..db import get_db, iso, utcnow
from ..models import (Barcode, BarcodePhoto, Batch, BatchItem, BatchMagazine, Magazine, MagazineEvent, PackCode, Product,
                      Transaction)
from ..services import code_for_product, magazine_dict, magazine_states, on_hand, product_dict, spec_text

router = APIRouter(prefix="/api/batches", dependencies=[Depends(security.require_auth)])

MAX_QTY = 99999


class NewBatch(BaseModel):
    kind: str = Field(pattern=r"^(in|out)$")


class ScanIn(BaseModel):
    code: str
    quantity: int = Field(default=1, ge=1, le=MAX_QTY)


class ProductIn(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=MAX_QTY)


class QtyIn(BaseModel):
    quantity: int = Field(ge=0, le=MAX_QTY)


def _code_dict(db: Session, id: int, code: str, quantity: int) -> dict:
    bc = db.get(Barcode, code)
    prod = product_dict(bc.product) if bc else None
    if prod and bc.product:
        prod["spec"] = spec_text(bc.product)
    return {
        "id": id,
        "code": code,
        "quantity": quantity,
        "product": prod,
        "on_hand": on_hand(db, code),
        "has_photo": db.get(BarcodePhoto, code) is not None,
    }


def _item_dict(db: Session, item: BatchItem) -> dict:
    return _code_dict(db, item.id, item.code, item.quantity)


def _magazine_dicts(db: Session, b: Batch) -> list[dict]:
    states = magazine_states(db)
    return [magazine_dict(db.get(Magazine, m.serial), states) for m in b.magazines]


def _batch_dict(db: Session, b: Batch) -> dict:
    return {
        "id": b.id,
        "kind": b.kind,
        "status": b.status,
        "created_at": iso(b.created_at),
        "items": [_item_dict(db, i) for i in b.items],
        "magazines": _magazine_dicts(db, b),
    }


def _draft(db: Session, batch_id: int) -> Batch:
    b = db.get(Batch, batch_id)
    if not b:
        raise HTTPException(404, "Batch not found")
    if b.status != "draft":
        raise HTTPException(409, f"Batch already {b.status}")
    return b


def current_draft(db: Session) -> Batch | None:
    return db.scalar(select(Batch).where(Batch.status == "draft").order_by(Batch.id.desc()))


@router.get("/current")
def get_current(db: Session = Depends(get_db)):
    b = current_draft(db)
    return _batch_dict(db, b) if b else None


@router.get("/recent-out")
def recent_out(days: int = Query(default=7, ge=1, le=60), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(Transaction).where(Transaction.kind == "out", Transaction.ts >= since)
                      .order_by(Transaction.id.desc()).limit(100))
    return [{**_code_dict(db, t.id, t.code, -t.boxes), "ts": iso(t.ts)} for t in rows]


@router.post("")
def start(body: NewBatch, db: Session = Depends(get_db)):
    b = current_draft(db)
    if b and b.kind == body.kind:
        return _batch_dict(db, b)
    if b and (b.items or b.magazines):
        raise HTTPException(409, "An unfinished batch of the other kind exists")
    if b:
        db.delete(b)
        db.flush()
    b = Batch(kind=body.kind)
    db.add(b)
    db.commit()
    return _batch_dict(db, b)


@router.post("/{batch_id}/scan")
def scan(batch_id: int, body: ScanIn, db: Session = Depends(get_db)):
    b = _draft(db, batch_id)
    try:
        code = normalize_code(body.code)
    except ValueError:
        raise HTTPException(400, "That doesn't look like a valid barcode")
    if is_magazine_code(code):
        return _scan_magazine(db, b, code)
    pack = db.get(PackCode, code)
    if pack:
        item = _add_item(b, code_for_product(db, pack.product_id), min(MAX_QTY, pack.quantity * body.quantity))
        db.commit()
        return {"item": _item_dict(db, item), "known": True, "new_code": False, "pack": pack.quantity}
    new_code = db.get(Barcode, code) is None  # first time this code has ever been seen
    if new_code:
        db.add(Barcode(code=code))
        db.flush()
    item = _add_item(b, code, body.quantity)
    db.commit()
    return {"item": _item_dict(db, item), "known": _is_known(db, code), "new_code": new_code}


def _scan_magazine(db: Session, b: Batch, code: str) -> dict:
    m = db.get(Magazine, code)
    if not m:
        raise HTTPException(404, f"{code} is not a magazine in the system")
    problem = _magazine_problem(db, b, code)
    if problem:
        raise HTTPException(409, problem)
    if any(x.serial == code for x in b.magazines):
        raise HTTPException(409, f"{code} is already on the list")
    b.magazines.append(BatchMagazine(serial=code))
    db.commit()
    return {"magazine": magazine_dict(m, magazine_states(db)), "known": True, "new_code": False}


def _magazine_problem(db: Session, b: Batch, code: str) -> str | None:
    status = magazine_states(db).get(code, ("in", None))[0]
    if status == b.kind:
        return f"{code} is already checked {b.kind}"
    return None


@router.delete("/{batch_id}/magazines/{serial}")
def remove_magazine(batch_id: int, serial: str, db: Session = Depends(get_db)):
    b = _draft(db, batch_id)
    row = next((m for m in b.magazines if m.serial == serial.upper()), None)
    if not row:
        raise HTTPException(404, "Magazine not on the list")
    b.magazines.remove(row)
    db.commit()
    return _batch_dict(db, b)


def _add_item(b: Batch, code: str, quantity: int) -> BatchItem:
    item = next((i for i in b.items if i.code == code), None)
    if item:
        item.quantity = min(MAX_QTY, item.quantity + quantity)
    else:
        item = BatchItem(batch_id=b.id, code=code, quantity=quantity)
        b.items.append(item)
    return item


@router.post("/{batch_id}/product")
def add_product(batch_id: int, body: ProductIn, db: Session = Depends(get_db)):
    b = _draft(db, batch_id)
    if not db.get(Product, body.product_id):
        raise HTTPException(404, "Product not found")
    item = _add_item(b, code_for_product(db, body.product_id), body.quantity)
    db.commit()
    return {"item": _item_dict(db, item), "known": True, "new_code": False}


def _is_known(db: Session, code: str) -> bool:
    bc = db.get(Barcode, code)
    return bool(bc and bc.product_id)


@router.patch("/{batch_id}/items/{item_id}")
def set_quantity(batch_id: int, item_id: int, body: QtyIn, db: Session = Depends(get_db)):
    b = _draft(db, batch_id)
    item = next((i for i in b.items if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "Item not found")
    if body.quantity == 0:
        b.items.remove(item)
    else:
        item.quantity = body.quantity
    db.commit()
    return _batch_dict(db, b)


@router.post("/{batch_id}/finish")
def finish(batch_id: int, db: Session = Depends(get_db)):
    b = _draft(db, batch_id)
    if not b.items and not b.magazines:
        raise HTTPException(400, "Nothing to record")
    stale = [p for p in (_magazine_problem(db, b, m.serial) for m in b.magazines) if p]
    if stale:
        raise HTTPException(409, "; ".join(stale) + ". Remove it from the list.")
    now = utcnow()
    sign = 1 if b.kind == "in" else -1
    boxes = rounds = 0  # products counted by the single round (1 per box) are reported as rounds
    for i in b.items:
        db.add(
            Transaction(ts=now, code=i.code, boxes=sign * i.quantity, kind=b.kind, batch_id=b.id)
        )
        bc = db.get(Barcode, i.code)
        if bc and bc.product and bc.product.rounds_per_box == 1:
            rounds += i.quantity
        else:
            boxes += i.quantity
    for m in b.magazines:
        db.add(MagazineEvent(ts=now, serial=m.serial, kind=b.kind, batch_id=b.id))
    b.status, b.finished_at = "finished", now
    db.commit()
    return {"ok": True, "kind": b.kind, "items": len(b.items), "boxes": boxes, "rounds": rounds, "magazines": len(b.magazines)}


@router.post("/{batch_id}/cancel")
def cancel(batch_id: int, db: Session = Depends(get_db)):
    b = _draft(db, batch_id)
    b.status = "cancelled"
    b.items.clear()
    b.magazines.clear()
    db.commit()
    return {"ok": True}
