import io
import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import segno
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Barcode, PackCode
from .services import LABEL_PREFIX, product_dict, spec_text

TAPE_PT = 34.0
PRINT_PT = 28.0
LENGTH_MARGIN_PT = 2.0
CELL_SIZES_PT = (1.2, 1.0, 0.8, 0.6)
TEXT_GAP_PT = 3.0
CHAR_WIDTH_EM = 0.55
COLUMNS = ["Code", "Type", "Caliber", "Product", "Details", "Total"]

NAMESPACES = " ".join(f'xmlns:{p}="http://schemas.brother.info/ptouch/2007/lbx/{p if p != "pt" else "main"}"'
                      for p in ("pt", "style", "text", "draw", "image", "barcode", "database", "table"))


def label_rows(db: Session) -> list[dict]:
    rows = []
    for k in db.scalars(select(PackCode)):
        rows.append(_row(k.code, "Pack", k.product, f"{k.quantity} {'rounds' if k.product.rounds_per_box == 1 else 'boxes'}"))
    for b in db.scalars(select(Barcode).where(Barcode.product_id.is_not(None), Barcode.code.like(f"{LABEL_PREFIX}%"))):
        if b.code[len(LABEL_PREFIX):].isdigit():
            rows.append(_row(b.code, "Product", b.product, ""))
    return sorted(rows, key=lambda r: (r["caliber"].lower(), r["product"].lower(), r["type"], r["code"]))


def _row(code, kind, product, total):
    d = product_dict(product)
    return {"code": code, "type": kind, "caliber": d["caliber"], "product": d["label"],
            "details": spec_text(product).replace(" · ", ", "), "total": total}


def qr_layout(code: str) -> dict:
    modules = segno.make(code, error="q", micro=False).symbol_size(border=0)[0]
    cell = next((c for c in CELL_SIZES_PT if modules * c <= PRINT_PT), CELL_SIZES_PT[-1])
    return {"cell": cell, "side": round(modules * cell, 1)}


def text_lines(row: dict) -> list[str]:
    return [x for x in (row["caliber"], row["product"], f"Total of {row['total']}" if row["total"] else "") if x]


def _text_object(n: int, text: str, x: float, y: float, w: float, h: float, size: float, bold: bool) -> str:
    font = f'<text:logFont name="Arial" width="0.0pt" italic="false" weight="{700 if bold else 400}" charSet="1" pitchAndFamily="{{pf}}"/><text:fontExt effect="NOEFFECT" underline="0" strikeout="0" size="{size}pt" orgSize="{size}pt" textColor="#000000"/>'
    return (f'<text:text><pt:objectStyle x="{x}pt" y="{y}pt" width="{w}pt" height="{h}pt" backColor="#FFFFFF" ropMode="COPYPEN" angle="0" anchor="TOPLEFT" flip="NONE">'
            '<pt:pen style="NULL" widthX="0.50pt" widthY="0.50pt" color="#000000"/><pt:brush style="NULL" color="#000000" id="0"/>'
            f'<pt:expanded objectName="Text{n}" ID="0" lock="0" templateMergeTarget="LABELLIST" templateMergeType="NONE" templateMergeID="0" dbMergeFieldStyleName="" linkStatus="NONE" linkID="0"/></pt:objectStyle>'
            f'<text:ptFontInfo>{font.format(pf=34)}</text:ptFontInfo>'
            '<text:textControl control="LONGTEXTFIXED" clipFrame="false" aspectNormal="true" shrink="true" autoLF="true" avoidImage="false"/>'
            '<text:textAlign horizontalAlignment="LEFT" verticalAlignment="CENTER" inLineAlignment="BASELINE"/>'
            f'<text:textStyle vertical="false" nullBlock="false" charSpace="0" lineSpace="0" orgPoint="{size}pt"/>'
            f'<pt:data>{escape(text)}</pt:data>'
            f'<text:stringItem charLen="{len(text)}"><text:ptFontInfo>{font.format(pf=0)}</text:ptFontInfo></text:stringItem></text:text>')


def lbx_bytes(row: dict) -> bytes:
    code = row["code"]
    q = qr_layout(code)
    lines = text_lines(row)
    size = 10.0 if len(lines) <= 2 else 7.5
    line_h = round(PRINT_PT / len(lines), 1)
    margin_side = round((TAPE_PT - PRINT_PT) / 2, 1)
    text_x = round(LENGTH_MARGIN_PT + q["side"] + TEXT_GAP_PT, 1)
    text_w = round(max(len(t) for t in lines) * size * CHAR_WIDTH_EM + 4, 1)
    length = round(text_x + text_w + LENGTH_MARGIN_PT, 1)
    qr_y = round(margin_side + (PRINT_PT - q["side"]) / 2, 1)
    texts = "".join(_text_object(i + 1, t, text_x, round(margin_side + i * line_h, 1), text_w, line_h, size, i == 0)
                    for i, t in enumerate(lines))
    label = f"""<?xml version="1.0" encoding="UTF-8"?>
<pt:document {NAMESPACES} version="1.1" generator="Quartermaster"><pt:body currentSheet="Sheet 1"><style:sheet name="Sheet 1"><style:paper media="0" width="{TAPE_PT}pt" height="{length}pt" marginLeft="{margin_side}pt" marginTop="{LENGTH_MARGIN_PT}pt" marginRight="{margin_side}pt" marginBottom="{LENGTH_MARGIN_PT}pt" orientation="landscape" autoLength="false" monochromeDisplay="true" paperColor="#FFFFFF" paperInk="#000000" split="1" format="" backgroundTheme="0" printerName="Brother PT-E500"/><style:cutLine regularCut="0pt" freeCut=""/><style:backGround x="{LENGTH_MARGIN_PT}pt" y="{margin_side}pt" width="{round(length - 2 * LENGTH_MARGIN_PT, 1)}pt" height="{PRINT_PT}pt" brushStyle="NULL" brushId="0" color="#000000" backColor="#FFFFFF"/><pt:objects><barcode:barcode><pt:objectStyle x="{LENGTH_MARGIN_PT}pt" y="{qr_y}pt" width="{q['side']}pt" height="{q['side']}pt" backColor="#FFFFFF" ropMode="COPYPEN" angle="0" anchor="TOPLEFT" flip="NONE"><pt:pen style="NULL" widthX="0.50pt" widthY="0.50pt" color="#000000"/><pt:brush style="NULL" color="#000000" id="0"/><pt:expanded objectName="Bar Code1" ID="0" lock="0" templateMergeTarget="LABELLIST" templateMergeType="NONE" templateMergeID="0" dbMergeFieldStyleName="" linkStatus="NONE" linkID="0"/></pt:objectStyle><barcode:barcodeStyle protocol="QRCODE" lengths="{len(code)}" zeroFill="false" barWidth="{q['cell']}pt" barRatio="1:3" humanReadable="false" humanReadableAlignment="LEFT" checkDigit="false" autoLengths="false" margin="false" sameLengthBar="false" bearerBar="false"/><pt:data>{escape(code)}</pt:data><barcode:qrcodeStyle model="2" eccLevel="25%" cellSize="{q['cell']}pt" mbcs="932" removeCharKind="0" removeCharString="" joint="1" jointSpace="8" jointVertically="false" version="auto" changeVersionDrag="false"/></barcode:barcode>{texts}</pt:objects></style:sheet></pt:body></pt:document>"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prop = f"""<?xml version="1.0" encoding="UTF-8"?>
<meta:properties xmlns:meta="http://schemas.brother.info/ptouch/2007/lbx/meta" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"><meta:appName>Quartermaster</meta:appName><dc:title></dc:title><dc:subject></dc:subject><dc:creator>Quartermaster</dc:creator><meta:keyword></meta:keyword><dc:description></dc:description><meta:template></meta:template><dcterms:created>{now}</dcterms:created><dcterms:modified>{now}</dcterms:modified><meta:lastModifiedBy>Quartermaster</meta:lastModifiedBy></meta:properties>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("label.xml", label)
        z.writestr("prop.xml", prop)
    return buf.getvalue()


def file_stem(row: dict) -> str:
    text = " ".join(x for x in (row["code"], row["caliber"], row["product"], row["total"]) if x)
    return re.sub(r"[^A-Za-z0-9 ._-]+", "", text).strip()[:120] or row["code"]
