"""Your own labels for ammo that has no UPC: allocate codes and render Code 128 / QR."""
import io

import barcode as pybarcode
import segno
from barcode.writer import SVGWriter
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import security
from ..codes import normalize_code
from ..db import get_db
from ..models import Barcode, Product, Setting

router = APIRouter(prefix="/api/labels", dependencies=[Depends(security.require_auth)])

PREFIX = "QM"
COUNTER_KEY = "label_counter"


class AllocateIn(BaseModel):
    count: int = Field(ge=1, le=100)
    product_id: int | None = None


@router.post("/allocate")
def allocate(body: AllocateIn, db: Session = Depends(get_db)):
    """Codes for `count` labels.

    With a product: the product keeps one label code, so its existing QM code is reused (the
    labels are copies of it); only a product with no label yet gets a new code.
    Without a product: `count` fresh, unique codes to identify later."""
    if body.product_id is not None and not db.get(Product, body.product_id):
        raise HTTPException(400, "Unknown product")
    if body.product_id is not None:
        mine = db.scalars(select(Barcode.code).where(Barcode.product_id == body.product_id,
                                                      Barcode.code.like(f"{PREFIX}%")).order_by(Barcode.code))
        existing = next((c for c in mine if c[len(PREFIX):].isdigit()), None)
        if existing:
            return {"codes": [existing] * body.count, "reused": True}
    row = db.get(Setting, COUNTER_KEY)
    n = int(row.value) if row else 0
    codes = []
    fresh = 1 if body.product_id is not None else body.count
    while len(codes) < fresh:
        n += 1
        code = f"{PREFIX}{n:06d}"
        if db.get(Barcode, code):
            continue
        db.add(Barcode(code=code, product_id=body.product_id))
        codes.append(code)
    if row:
        row.value = str(n)
    else:
        db.add(Setting(key=COUNTER_KEY, value=str(n)))
    db.commit()
    if body.product_id is not None:
        codes = codes * body.count
    return {"codes": codes, "reused": False}


@router.get("/render")
def render(code: str, type: str = Query(default="code128", pattern="^(code128|qr)$")):
    try:
        code = normalize_code(code)
    except ValueError:
        raise HTTPException(400, "Invalid code")
    buf = io.BytesIO()
    if type == "qr":
        # micro=False: segno would otherwise pick Micro QR for short strings, which many scanners can't read.
        segno.make(code, error="m", micro=False).save(buf, kind="svg", scale=4, border=1, xmldecl=False)
    else:
        pybarcode.get("code128", code, writer=SVGWriter()).write(
            buf, options={"write_text": False, "module_height": 12.0, "module_width": 0.33, "quiet_zone": 2.0}
        )
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})
