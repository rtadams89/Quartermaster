"""Kiosk check-in / check-out: scans are queued in a draft batch, then committed."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import security
from ..codes import normalize_code
from ..db import get_db, iso, utcnow
from ..models import Barcode, BarcodePhoto, Batch, BatchItem, Transaction
from ..services import on_hand, product_dict, spec_text

router = APIRouter(prefix="/api/batches", dependencies=[Depends(security.require_auth)])

MAX_QTY = 999


class NewBatch(BaseModel):
    kind: str = Field(pattern=r"^(in|out)$")


class ScanIn(BaseModel):
    code: str
    quantity: int = Field(default=1, ge=1, le=MAX_QTY)


class QtyIn(BaseModel):
    quantity: int = Field(ge=0, le=MAX_QTY)


def _item_dict(db: Session, item: BatchItem) -> dict:
    bc = db.get(Barcode, item.code)
    prod = product_dict(bc.product) if bc else None
    if prod and bc.product:
        prod["spec"] = spec_text(bc.product)
    return {
        "id": item.id,
        "code": item.code,
        "quantity": item.quantity,
        "product": prod,
        "on_hand": on_hand(db, item.code),
        "has_photo": db.get(BarcodePhoto, item.code) is not None,
    }


def _batch_dict(db: Session, b: Batch) -> dict:
    return {
        "id": b.id,
        "kind": b.kind,
        "status": b.status,
        "created_at": iso(b.created_at),
        "items": [_item_dict(db, i) for i in b.items],
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


@router.post("")
def start(body: NewBatch, db: Session = Depends(get_db)):
    b = current_draft(db)
    if b and b.kind == body.kind:
        return _batch_dict(db, b)
    if b and b.items:
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
    new_code = db.get(Barcode, code) is None  # first time this code has ever been seen
    if new_code:
        db.add(Barcode(code=code))
        db.flush()
    item = next((i for i in b.items if i.code == code), None)
    if item:
        item.quantity = min(MAX_QTY, item.quantity + body.quantity)
    else:
        item = BatchItem(batch_id=b.id, code=code, quantity=body.quantity)
        b.items.append(item)
    db.commit()
    return {"item": _item_dict(db, item), "known": _is_known(db, code), "new_code": new_code}


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
    if not b.items:
        raise HTTPException(400, "Nothing to record")
    now = utcnow()
    sign = 1 if b.kind == "in" else -1
    total = 0
    for i in b.items:
        db.add(
            Transaction(ts=now, code=i.code, boxes=sign * i.quantity, kind=b.kind, batch_id=b.id)
        )
        total += i.quantity
    b.status, b.finished_at = "finished", now
    db.commit()
    return {"ok": True, "kind": b.kind, "items": len(b.items), "boxes": total}


@router.post("/{batch_id}/cancel")
def cancel(batch_id: int, db: Session = Depends(get_db)):
    b = _draft(db, batch_id)
    b.status = "cancelled"
    b.items.clear()
    db.commit()
    return {"ok": True}
