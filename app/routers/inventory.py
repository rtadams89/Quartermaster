"""Inventory views, ledger history, manual adjustments, CSV export."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import security
from ..codes import normalize_code
from ..db import get_db, iso
from ..models import Barcode, Batch, BatchItem, Transaction
from ..services import (box_count, drill, inventory_by_product, locate, low_stock, on_hand_by_code, out_of_stock_calibers,
                        price_range, stock_money)

router = APIRouter(prefix="/api", dependencies=[Depends(security.require_auth)])


@router.get("/inventory/drill")
def inventory_drill(
    caliber: str | None = None,
    weight: str | None = None,
    manufacturer: str | None = None,
    by_manufacturer: bool = False,
    include_empty: bool = False,
    db: Session = Depends(get_db),
):
    return drill(db, caliber, weight, manufacturer, by_manufacturer, include_empty)


@router.get("/out-of-stock")
def out_of_stock(db: Session = Depends(get_db)):
    """Calibers you keep but have none of right now."""
    return [{"id": c.id, "name": c.name} for c in out_of_stock_calibers(db)]


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
        "total_boxes": box_count(products) + sum(u["boxes"] for u in unidentified),
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
        "single": prod.rounds_per_box == 1 if prod else False,
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


class ClearHistoryIn(BaseModel):
    confirm: str


@router.post("/history/clear")
def clear_history(body: ClearHistoryIn, db: Session = Depends(get_db)):
    """Erase the history log but keep what is on hand. Stock is the sum of the history, so each code's
    total is rewritten as one 'opening balance' entry (codes at zero get none). Products, barcodes,
    photos, alert levels and any scan session still in progress are left alone."""
    if body.confirm != "CLEAR":
        raise HTTPException(400, "Type CLEAR to confirm")
    stock = on_hand_by_code(db)
    removed = db.scalar(select(func.count(Transaction.id))) or 0
    db.execute(delete(Transaction))
    done = select(Batch.id).where(Batch.status != "draft")
    db.execute(delete(BatchItem).where(BatchItem.batch_id.in_(done)))
    db.execute(delete(Batch).where(Batch.status != "draft"))
    kept = 0
    for code, boxes in sorted(stock.items()):
        if boxes:
            db.add(Transaction(code=code, boxes=boxes, kind="adjust", note="Opening balance (history cleared)"))
            kept += 1
    db.commit()
    return {"removed": removed, "kept": kept}


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
            p["caliber"], p["brand"], p["name"], "N/A" if p["weight_na"] else p["bullet_weight_gr"] or "", p["bullet_type"],
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
