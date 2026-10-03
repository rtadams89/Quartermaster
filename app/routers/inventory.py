"""Inventory views, ledger history, manual adjustments, CSV export."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import security
from ..codes import normalize_code
from ..db import get_db, iso
from ..models import Barcode, Transaction
from ..services import drill, inventory_by_product, locate, low_stock, price_range, stock_money

router = APIRouter(prefix="/api", dependencies=[Depends(security.require_auth)])


@router.get("/inventory/drill")
def inventory_drill(
    caliber: str | None = None,
    weight: str | None = None,
    manufacturer: str | None = None,
    by_manufacturer: bool = False,
    db: Session = Depends(get_db),
):
    return drill(db, caliber, weight, manufacturer, by_manufacturer)


@router.get("/low-stock")
def low_stock_report(db: Session = Depends(get_db)):
    return low_stock(db)


@router.get("/inventory/code/{code}")
def inventory_code(code: str, db: Session = Depends(get_db)):
    try:
        code = normalize_code(code)
    except ValueError:
        raise HTTPException(400, "Invalid code")
    return locate(db, code)


@router.get("/inventory/items")
def inventory_items(
    caliber_id: int | None = None,
    q: str | None = None,
    include_zero: bool = False,
    db: Session = Depends(get_db),
):
    products, unidentified = inventory_by_product(db)
    if not include_zero:
        products = [p for p in products if p["boxes"] != 0]
        unidentified = [u for u in unidentified if u["boxes"] != 0]
    if caliber_id:
        products = [p for p in products if p["caliber_id"] == caliber_id]
        unidentified = []
    if q:
        ql = q.lower()
        products = [
            p
            for p in products
            if ql in " ".join([p["label"], p["caliber"] or "", p["bullet_type"], *[c["code"] for c in p["codes"]]]).lower()
        ]
        unidentified = [u for u in unidentified if ql in u["code"].lower()]
    products.sort(key=lambda p: ((p["caliber"] or "").lower(), p["bullet_weight_gr"] or 0, p["label"].lower()))
    return {
        "products": products,
        "unidentified": unidentified,
        "total_boxes": sum(p["boxes"] for p in products) + sum(u["boxes"] for u in unidentified),
        "total_rounds": sum(p["rounds"] for p in products),
        "price": price_range(products),
        "total_value": stock_money(products)["value"],
    }


@router.get("/inventory/unidentified")
def unidentified(db: Session = Depends(get_db)):
    _, unid = inventory_by_product(db)
    unid.sort(key=lambda u: u["last_activity"] or u["first_seen_at"], reverse=True)
    return unid


# ------------------------------------------------------------------- history
def _tx_dict(t: Transaction, bc: Barcode | None) -> dict:
    prod = bc.product if bc else None
    return {
        "id": t.id,
        "ts": iso(t.ts),
        "code": t.code,
        "boxes": t.boxes,
        "kind": t.kind,
        "batch_id": t.batch_id,
        "note": t.note,
        "product": (" ".join(x for x in (prod.brand, prod.name) if x) or "(unnamed)") if prod else None,
        "caliber": prod.caliber.name if prod else None,
    }


@router.get("/transactions")
def transactions(
    code: str | None = None,
    kind: str | None = Query(default=None, pattern="^(in|out|adjust)$"),
    before_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(Transaction).order_by(Transaction.id.desc()).limit(limit)
    if code:
        try:
            stmt = stmt.where(Transaction.code == normalize_code(code))
        except ValueError:
            return []
    if kind:
        stmt = stmt.where(Transaction.kind == kind)
    if before_id:
        stmt = stmt.where(Transaction.id < before_id)
    return [_tx_dict(t, db.get(Barcode, t.code)) for t in db.scalars(stmt)]


class AdjustIn(BaseModel):
    code: str
    boxes: int = Field(ge=-100000, le=100000)
    note: str = Field(default="", max_length=300)


@router.post("/adjustments")
def adjust(body: AdjustIn, db: Session = Depends(get_db)):
    """Manual correction. The ledger is append-only, so mistakes are fixed by adding an entry."""
    if body.boxes == 0:
        raise HTTPException(400, "Adjustment can't be zero")
    try:
        code = normalize_code(body.code)
    except ValueError:
        raise HTTPException(400, "Invalid code")
    if not db.get(Barcode, code):
        raise HTTPException(404, "Code not found")
    t = Transaction(code=code, boxes=body.boxes, kind="adjust", note=body.note.strip())
    db.add(t)
    db.commit()
    return _tx_dict(t, db.get(Barcode, code))


class StockIn(BaseModel):
    code: str
    boxes: int = Field(ge=1, le=100000)
    note: str = Field(default="", max_length=300)


@router.post("/stock")
def add_stock(body: StockIn, db: Session = Depends(get_db)):
    """Add boxes of a known product from the admin site (same as scanning them in at the kiosk)."""
    try:
        code = normalize_code(body.code)
    except ValueError:
        raise HTTPException(400, "Invalid code")
    bc = db.get(Barcode, code)
    if not bc or bc.product_id is None:
        raise HTTPException(404, "That code isn't attached to a product")
    t = Transaction(code=code, boxes=body.boxes, kind="in", note=body.note.strip())
    db.add(t)
    db.commit()
    return _tx_dict(t, bc)


# -------------------------------------------------------------------- export
def _csv(rows: list[list], header: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/inventory.csv")
def export_inventory(db: Session = Depends(get_db)):
    products, unid = inventory_by_product(db)
    rows = [
        [
            p["caliber"], p["brand"], p["name"], p["bullet_weight_gr"] or "", p["bullet_type"],
            p["rounds_per_box"], " ".join(c["code"] for c in p["codes"]), p["boxes"], p["rounds"],
        ]
        for p in products
        if p["boxes"] != 0
    ]
    rows += [["(unidentified)", "", "", "", "", "", u["code"], u["boxes"], ""] for u in unid if u["boxes"] != 0]
    return _csv(
        rows,
        ["caliber", "manufacturer", "name", "weight_gr", "type", "rounds_per_box", "codes", "boxes", "rounds"],
        "quartermaster-inventory.csv",
    )


@router.get("/export/transactions.csv")
def export_transactions(db: Session = Depends(get_db)):
    rows = []
    for t in db.scalars(select(Transaction).order_by(Transaction.id)):
        d = _tx_dict(t, db.get(Barcode, t.code))
        rows.append([d["ts"], d["kind"], d["code"], d["caliber"] or "", d["product"] or "", d["boxes"], d["note"]])
    return _csv(rows, ["timestamp_utc", "kind", "code", "caliber", "product", "boxes", "note"], "quartermaster-transactions.csv")
