"""Your own labels for ammo: resolve picks to codes, render Code 128 / QR, and export for P-touch Editor."""
import csv
import io
import zipfile

import barcode as pybarcode
import segno
from barcode.writer import SVGWriter
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import labelrows, ptouch, security
from ..codes import normalize_code
from ..db import get_db
from ..csvsafe import safe_row

router = APIRouter(prefix="/api/labels", dependencies=[Depends(security.require_auth)])

class ItemIn(BaseModel):
    code: str | None = None
    product_id: int | None = None
    pack_qty: int | None = Field(default=None, ge=2, le=99999)
    copies: int = Field(default=1, ge=1, le=100)


class JobIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=200)
    mode: str = Field(default="one", pattern="^(one|many)$")


def resolved(db: Session, body: JobIn) -> list[dict]:
    try:
        rows = labelrows.resolve_rows(db, body.items)
    except labelrows.LabelError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    return rows


@router.post("/resolve")
def resolve(body: JobIn, db: Session = Depends(get_db)):
    return {"rows": resolved(db, body)}


@router.post("/ptouch")
def ptouch_files(body: JobIn, db: Session = Depends(get_db)):
    rows = resolved(db, body)
    if body.mode == "one":
        pages = [r for r in rows for _ in range(r["copies"])]
        return Response(ptouch.lbx_bytes(pages), media_type="application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="quartermaster-labels.lbx"'})
    unique: dict[str, dict] = {}
    for r in rows:
        unique.setdefault(r["code"], {**r, "copies": 0})["copies"] += r["copies"]
    files = {}
    for r in unique.values():
        times = f" x{r['copies']}" if r["copies"] > 1 else ""
        files[f"{ptouch.file_stem(r)}{times}.lbx"] = ptouch.lbx_bytes([r])
    if len(files) == 1:
        name, data = next(iter(files.items()))
        return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="quartermaster-labels-ptouch.zip"'})


@router.post("/ptouch.csv")
def ptouch_csv(body: JobIn, db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ptouch.COLUMNS)
    for r in resolved(db, body):
        for _ in range(r["copies"]):
            w.writerow(safe_row([r["code"], r["type"], r["caliber"], r["product"], r["details"], r["total"]]))
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="quartermaster-labels.csv"'})


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
