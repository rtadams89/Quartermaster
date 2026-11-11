"""Your own labels for ammo that has no UPC: allocate codes and render Code 128 / QR."""
import io

import barcode as pybarcode
import segno
from barcode.writer import SVGWriter
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import security
from ..codes import normalize_code
from ..db import get_db
from ..models import Product
from ..services import new_label_codes, product_label_code

router = APIRouter(prefix="/api/labels", dependencies=[Depends(security.require_auth)])

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
