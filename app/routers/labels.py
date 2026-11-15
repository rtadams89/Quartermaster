"""Your own labels for ammo that has no UPC: allocate codes and render Code 128 / QR."""
import csv
import io
import zipfile

import barcode as pybarcode
import segno
from barcode.writer import SVGWriter
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ptouch, security
from ..codes import normalize_code
from ..db import get_db
from ..csvsafe import safe_row
from ..models import PackCode, Product
from ..services import new_label_codes, pack_code_for, product_label_code, product_dict, spec_text

router = APIRouter(prefix="/api/labels", dependencies=[Depends(security.require_auth)])

class AllocateIn(BaseModel):
    count: int = Field(ge=1, le=100)
    product_id: int | None = None
    pack_qty: int | None = Field(default=None, ge=2, le=99999)


@router.post("/allocate")
def allocate(body: AllocateIn, db: Session = Depends(get_db)):
    """Codes for `count` labels.

    With a product: the product keeps one label code, so its existing QM code is reused (the
    labels are copies of it); only a product with no label yet gets a new code.
    With a product and pack_qty: a pack code (one per product and quantity) that adds that many when scanned.
    Without a product: `count` fresh, unique codes to identify later."""
    if body.product_id is not None and not db.get(Product, body.product_id):
        raise HTTPException(400, "Unknown product")
    if body.pack_qty is not None:
        if body.product_id is None:
            raise HTTPException(400, "Choose a product for a pack label")
        code, reused = pack_code_for(db, body.product_id, body.pack_qty)
        db.commit()
        return {"codes": [code] * body.count, "reused": reused}
    if body.product_id is None:
        codes = new_label_codes(db, body.count)
        db.commit()
        return {"codes": codes, "reused": False}
    existing = product_label_code(db, body.product_id)
    if existing:
        return {"codes": [existing] * body.count, "reused": True}
    codes = new_label_codes(db, 1, body.product_id)
    db.commit()
    return {"codes": codes * body.count, "reused": False}


@router.get("/packs")
def packs(db: Session = Depends(get_db)):
    rows = [{"code": r.code, "quantity": r.quantity, "product_id": r.product_id,
             "product": {**product_dict(r.product), "spec": spec_text(r.product)}}
            for r in db.scalars(select(PackCode))]
    return sorted(rows, key=lambda r: (r["product"]["caliber"].lower(), r["product"]["label"].lower(), r["quantity"]))


@router.delete("/packs/{code}")
def delete_pack(code: str, db: Session = Depends(get_db)):
    row = db.get(PackCode, code.upper())
    if not row:
        raise HTTPException(404, "Pack label not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/render")
def render(code: str, type: str = Query(default="code128", pattern="^(code128|qr)$")):
    try:
        code = normalize_code(code)
    except ValueError:
        raise HTTPException(400, "Invalid code")
    buf = io.BytesIO()
    if type == "qr":
        # micro=False: always a regular QR code
        segno.make(code, error="m", micro=False).save(buf, kind="svg", scale=4, border=1, xmldecl=False)
    else:
        pybarcode.get("code128", code, writer=SVGWriter()).write(
            buf, options={"write_text": False, "module_height": 12.0, "module_width": 0.33, "quiet_zone": 2.0}
        )
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/ptouch.csv")
def ptouch_csv(db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ptouch.COLUMNS)
    for r in ptouch.label_rows(db):
        w.writerow(safe_row([r["code"], r["type"], r["caliber"], r["product"], r["details"], r["total"]]))
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="quartermaster-labels.csv"'})


@router.get("/ptouch.zip")
def ptouch_zip(db: Session = Depends(get_db)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for r in ptouch.label_rows(db):
            z.writestr(f"{ptouch.file_stem(r)}.lbx", ptouch.lbx_bytes(r))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="quartermaster-labels-ptouch.zip"'})


@router.get("/ptouch/{code}.lbx")
def ptouch_lbx(code: str, db: Session = Depends(get_db)):
    try:
        code = normalize_code(code)
    except ValueError:
        raise HTTPException(400, "Invalid code")
    row = next((r for r in ptouch.label_rows(db) if r["code"] == code), None)
    if not row:
        raise HTTPException(404, "No label with that code")
    return Response(ptouch.lbx_bytes(row), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{code}.lbx"'})
