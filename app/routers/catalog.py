"""Calibers, products, and barcode <-> product assignment (admin site)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config, lookup, security
from ..codes import normalize_code
from ..db import get_db
from ..models import Barcode, BarcodePhoto, Caliber, Product, Transaction, UpcLookup
from .photos import process_image, store_photo
from ..services import minimums, photo_codes, product_dict, set_minimum, spec_text

router = APIRouter(prefix="/api", dependencies=[Depends(security.require_auth)])


# ------------------------------------------------------------------ calibers
MIN_ROUNDS = Field(default=None, ge=0, le=1_000_000)  # low-stock level; empty or 0 means none


class CaliberIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    active: bool = True


class CaliberPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    active: bool | None = None
    min_rounds: int | None = MIN_ROUNDS  # sent as null to clear it


class ReorderIn(BaseModel):
    ids: list[int]


def _caliber_dict(c: Caliber, count: int = 0, floor: int | None = None) -> dict:
    return {"id": c.id, "name": c.name, "active": c.active, "sort_order": c.sort_order, "products": count,
            "min_rounds": floor}


@router.get("/calibers")
def list_calibers(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Product.caliber_id, func.count(Product.id)).group_by(Product.caliber_id)).all())
    mins = minimums(db)
    rows = db.scalars(select(Caliber).order_by(Caliber.sort_order, Caliber.name))
    return [_caliber_dict(c, counts.get(c.id, 0), mins.get(("caliber", c.id))) for c in rows]


@router.post("/calibers")
def create_caliber(body: CaliberIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.scalar(select(Caliber).where(func.lower(Caliber.name) == name.lower())):
        raise HTTPException(409, "That caliber already exists")
    top = db.scalar(select(func.coalesce(func.max(Caliber.sort_order), 0))) or 0
    c = Caliber(name=name, active=body.active, sort_order=top + 10)
    db.add(c)
    db.commit()
    return _caliber_dict(c)


@router.patch("/calibers/{cid}")
def update_caliber(cid: int, body: CaliberPatch, db: Session = Depends(get_db)):
    c = db.get(Caliber, cid)
    if not c:
        raise HTTPException(404, "Caliber not found")
    if body.name is not None:
        name = body.name.strip()
        dup = db.scalar(
            select(Caliber).where(func.lower(Caliber.name) == name.lower(), Caliber.id != cid)
        )
        if dup:
            raise HTTPException(409, "That caliber already exists")
        c.name = name
    if body.active is not None:
        c.active = body.active
    if "min_rounds" in body.model_fields_set:
        set_minimum(db, "caliber", cid, body.min_rounds)
    db.commit()
    return _caliber_dict(c, floor=minimums(db).get(("caliber", cid)))


@router.post("/calibers/reorder")
def reorder_calibers(body: ReorderIn, db: Session = Depends(get_db)):
    for pos, cid in enumerate(body.ids):
        c = db.get(Caliber, cid)
        if c:
            c.sort_order = (pos + 1) * 10
    db.commit()
    return {"ok": True}


@router.delete("/calibers/{cid}")
def delete_caliber(cid: int, db: Session = Depends(get_db)):
    c = db.get(Caliber, cid)
    if not c:
        raise HTTPException(404, "Caliber not found")
    if db.scalar(select(func.count(Product.id)).where(Product.caliber_id == cid)):
        raise HTTPException(409, "Products use this caliber. Mark it inactive instead, or move those products first.")
    set_minimum(db, "caliber", cid, None)
    db.delete(c)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ products
class ProductIn(BaseModel):
    caliber_id: int
    brand: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=120)
    bullet_weight_gr: float | None = Field(default=None, gt=0, le=5000)
    bullet_type: str = Field(default="", max_length=40)
    rounds_per_box: int = Field(gt=0, le=10000)
    cost_per_box: float | None = Field(default=None, ge=0)
    notes: str = ""
    min_rounds: int | None = MIN_ROUNDS


def _product_full(db: Session, p: Product, photos: set[str] | None = None, mins: dict | None = None) -> dict:
    photos = photo_codes(db) if photos is None else photos
    mins = minimums(db) if mins is None else mins
    d = product_dict(p)
    d["min_rounds"] = mins.get(("product", p.id))
    d["spec"] = spec_text(p)
    d["codes"] = [b.code for b in sorted(p.barcodes, key=lambda b: b.code)]
    d["photo_codes"] = [c for c in d["codes"] if c in photos]
    return d


@router.get("/products")
def list_products(caliber_id: int | None = None, q: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Product)
    if caliber_id:
        stmt = stmt.where(Product.caliber_id == caliber_id)
    out = []
    photos = photo_codes(db)
    mins = minimums(db)
    for p in db.scalars(stmt):
        d = _product_full(db, p, photos, mins)
        if q:
            hay = " ".join([d["label"], d["caliber"] or "", d["bullet_type"], *d["codes"]]).lower()
            if q.lower() not in hay:
                continue
        out.append(d)
    out.sort(key=lambda d: ((d["caliber"] or "").lower(), d["bullet_weight_gr"] or 0, d["label"].lower()))
    return out


def _check_caliber(db: Session, cid: int) -> None:
    if not db.get(Caliber, cid):
        raise HTTPException(400, "Unknown caliber")


@router.post("/products")
def create_product(body: ProductIn, db: Session = Depends(get_db)):
    _check_caliber(db, body.caliber_id)
    p = Product(**body.model_dump(exclude={"min_rounds"}))
    db.add(p)
    db.flush()
    set_minimum(db, "product", p.id, body.min_rounds)
    db.commit()
    return _product_full(db, p)


@router.put("/products/{pid}")
def update_product(pid: int, body: ProductIn, db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404, "Product not found")
    _check_caliber(db, body.caliber_id)
    for k, v in body.model_dump(exclude={"min_rounds"}).items():
        setattr(p, k, v)
    set_minimum(db, "product", pid, body.min_rounds)
    db.commit()
    return _product_full(db, p)


@router.delete("/products/{pid}")
def delete_product(pid: int, db: Session = Depends(get_db)):
    """Deleting a product does not lose history: its codes simply become unidentified again."""
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404, "Product not found")
    for b in p.barcodes:
        b.product_id = None
    set_minimum(db, "product", pid, None)
    db.delete(p)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ barcodes
class AssignIn(BaseModel):
    product_id: int | None


class CodeIn(BaseModel):
    code: str


def _norm(code: str) -> str:
    try:
        return normalize_code(code)
    except ValueError:
        raise HTTPException(400, "Invalid code")


@router.put("/barcodes/{code}")
def assign_barcode(code: str, body: AssignIn, db: Session = Depends(get_db)):
    """Point a code at a product (or at nothing, to make it unidentified again)."""
    code = _norm(code)
    bc = db.get(Barcode, code)
    if not bc:
        raise HTTPException(404, "Code not found")
    if body.product_id is not None and not db.get(Product, body.product_id):
        raise HTTPException(400, "Unknown product")
    bc.product_id = body.product_id
    db.commit()
    return {"ok": True}


@router.post("/products/{pid}/barcodes")
def add_barcode(pid: int, body: CodeIn, db: Session = Depends(get_db)):
    """Register a code to a product ahead of its first scan."""
    if not db.get(Product, pid):
        raise HTTPException(404, "Product not found")
    code = _norm(body.code)
    bc = db.get(Barcode, code)
    if bc and bc.product_id not in (None, pid):
        raise HTTPException(409, "That code already belongs to another product")
    if not bc:
        bc = Barcode(code=code)
        db.add(bc)
    bc.product_id = pid
    db.commit()
    return {"ok": True, "code": code}


@router.get("/lookup/{code}")
def lookup_code(code: str, db: Session = Depends(get_db)):
    """Ask UPCitemdb what a retail barcode is, with a guess at the product details."""
    code = _norm(code)
    if not config.UPC_LOOKUP:
        return {"enabled": False, "found": False}
    if not lookup.is_lookupable(code):
        return {"enabled": True, "found": False, "reason": "not a retail barcode"}
    try:
        row = lookup.lookup(db, code)
    except lookup.LookupFailed as e:
        raise HTTPException(503, str(e))
    if not row.found:
        return {"enabled": True, "found": False}
    return {"enabled": True, "found": True, "title": row.title, "brand": row.brand,
            "image": row.image_url, "suggestion": lookup.guess(db, row)}


@router.post("/lookup/{code}/photo")
def lookup_photo(code: str, db: Session = Depends(get_db)):
    """Keep the online listing's photo as this code's box photo, but only when it is a real
    product photo and the code has no photo yet. An existing (your own) photo is never replaced."""
    code = _norm(code)
    if not config.UPC_LOOKUP:
        return {"saved": False, "reason": "disabled"}
    if not db.get(Barcode, code):
        raise HTTPException(404, "Code not found")
    if db.get(BarcodePhoto, code):
        return {"saved": False, "reason": "has_photo"}
    row = db.get(UpcLookup, code)
    if not row or not row.found or not row.image_url:
        return {"saved": False, "reason": "no_image"}
    try:
        data = lookup.fetch_image(row.image_url)
    except lookup.LookupFailed:
        return {"saved": False, "reason": "unavailable"}
    if not lookup.looks_like_product_photo(data):
        return {"saved": False, "reason": "not_a_photo"}
    full, thumb = process_image(data)
    store_photo(db, code, full, thumb)
    return {"saved": True}


@router.delete("/barcodes/{code}")
def delete_barcode(code: str, db: Session = Depends(get_db)):
    code = _norm(code)
    bc = db.get(Barcode, code)
    if not bc:
        raise HTTPException(404, "Code not found")
    if db.scalar(select(func.count(Transaction.id)).where(Transaction.code == code)):
        raise HTTPException(409, "This code has history. Unassign it from the product instead.")
    photo = db.get(BarcodePhoto, code)
    if photo:
        db.delete(photo)
    db.delete(bc)
    db.commit()
    return {"ok": True}
