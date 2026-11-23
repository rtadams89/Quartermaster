import io
import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import segno

TAPE_PT = 33.6
PRINT_PT = 28.0
EDGE_PT = 2.8
START_PT = 5.6
EDITOR_MODULES = 25
CELL_PT = 1.2
QR_Y_PT = 1.8
TEXT_GAP_PT = 3.0
CHAR_WIDTH_EM = 0.55

NAMESPACES = " ".join(f'xmlns:{p}="http://schemas.brother.info/ptouch/2007/lbx/{p if p != "pt" else "main"}"'
                      for p in ("pt", "style", "text", "draw", "image", "barcode", "database", "table", "cable"))


def qr_layout(code: str) -> dict:
    modules = max(segno.make(code, error="q", micro=False).symbol_size(border=0)[0], EDITOR_MODULES)
    return {"cell": CELL_PT, "side": round(modules * CELL_PT, 1), "version": "auto"}


def text_lines(row: dict) -> list[str]:
    lines = row["lines"] if "lines" in row else [row["caliber"], row["product"], row["total"]]
    return [x for x in lines if x]


def _text_object(n: int, text: str, x: float, y: float, w: float, h: float, size: float, bold: bool) -> str:
    font = (f'<text:ptFontInfo><text:logFont name="Arial" width="0" italic="false" weight="{700 if bold else 400}" charSet="0" pitchAndFamily="34"/>'
            f'<text:fontExt effect="NOEFFECT" underline="0" strikeout="0" size="{size}pt" orgSize="{size}pt" textColor="#000000" textPrintColorNumber="1"/></text:ptFontInfo>')
    return (f'<text:text><pt:objectStyle x="{x}pt" y="{y}pt" width="{w}pt" height="{h}pt" backColor="#FFFFFF" backPrintColorNumber="0" ropMode="COPYPEN" angle="0" anchor="TOPLEFT" flip="NONE">'
            '<pt:pen style="NULL" widthX="0.5pt" widthY="0.5pt" color="#000000" printColorNumber="1"/><pt:brush style="NULL" color="#000000" printColorNumber="1" id="0"/>'
            f'<pt:expanded objectName="Text{n}" ID="0" lock="0" templateMergeTarget="LABELLIST" templateMergeType="NONE" templateMergeID="0" linkStatus="NONE" linkID="0"/></pt:objectStyle>'
            f'{font}'
            '<text:textControl control="LONGTEXTFIXED" clipFrame="false" aspectNormal="true" shrink="true" autoLF="true" avoidImage="false"/>'
            '<text:textAlign horizontalAlignment="LEFT" verticalAlignment="CENTER" inLineAlignment="BASELINE"/>'
            f'<text:textStyle vertical="false" nullBlock="false" charSpace="0" lineSpace="0" orgPoint="{size}pt" combinedChars="false"/>'
            f'<pt:data>{escape(text)}</pt:data>'
            f'<text:stringItem charLen="{len(text)}">{font}</text:stringItem></text:text>')


def _sheet(row: dict, n: int) -> str:
    code = row["code"]
    q = qr_layout(code)
    lines = text_lines(row)
    size = 10.0 if len(lines) <= 2 else 7.5
    line_h = round(PRINT_PT / len(lines), 1) if lines else PRINT_PT
    text_x = round(START_PT + q["side"] + TEXT_GAP_PT, 1)
    text_w = round(max((len(t) for t in lines), default=0) * size * CHAR_WIDTH_EM + 4, 1)
    bg_w = round(text_x + text_w - START_PT, 1) if lines else q["side"]
    texts = "".join(_text_object(i + 1, t, text_x, round(EDGE_PT + i * line_h, 1), text_w, line_h, size, i == 0 and not row.get("plain"))
                    for i, t in enumerate(lines))
    qr_y = QR_Y_PT
    return f"""<style:sheet name="Sheet {n}"><style:paper media="0" width="{TAPE_PT}pt" height="2834.4pt" marginLeft="3pt" marginTop="{START_PT}pt" marginRight="3pt" marginBottom="{START_PT}pt" orientation="landscape" autoLength="true" monochromeDisplay="true" printColorDisplay="false" printColorsID="0" paperColor="#FFFFFF" paperInk="#000000" split="1" format="259" backgroundTheme="0" printerID="25904" printerName="Brother PT-E500"/><style:cutLine regularCut="0pt" freeCut=""/><style:backGround x="{START_PT}pt" y="{EDGE_PT}pt" width="{bg_w}pt" height="{PRINT_PT}pt" brushStyle="NULL" brushId="0" userPattern="NONE" userPatternId="0" color="#000000" printColorNumber="1" backColor="#FFFFFF" backPrintColorNumber="0"/><pt:objects><barcode:barcode><pt:objectStyle x="{START_PT}pt" y="{qr_y}pt" width="{q['side']}pt" height="{q['side']}pt" backColor="#FFFFFF" backPrintColorNumber="0" ropMode="COPYPEN" angle="0" anchor="TOPLEFT" flip="NONE"><pt:pen style="NULL" widthX="0.5pt" widthY="0.5pt" color="#000000" printColorNumber="1"/><pt:brush style="NULL" color="#000000" printColorNumber="1" id="0"/><pt:expanded objectName="Bar Code1" ID="0" lock="4" templateMergeTarget="LABELLIST" templateMergeType="NONE" templateMergeID="0" linkStatus="NONE" linkID="0"/></pt:objectStyle><barcode:barcodeStyle protocol="QRCODE" lengths="{len(code)}" zeroFill="false" barWidth="{q['cell']}pt" barRatio="1:3" humanReadable="false" humanReadableAlignment="LEFT" checkDigit="false" autoLengths="false" margin="false" sameLengthBar="false" bearerBar="false"/><barcode:qrcodeStyle model="2" eccLevel="25%" cellSize="{q['cell']}pt" mbcs="1252" joint="1" version="{q['version']}"/><pt:data>{escape(code)}</pt:data></barcode:barcode>{texts}</pt:objects></style:sheet>"""


def lbx_bytes(rows: list[dict]) -> bytes:
    sheets = "".join(_sheet(r, i + 1) for i, r in enumerate(rows))
    label = f"""<?xml version="1.0" encoding="UTF-8"?><pt:document {NAMESPACES} version="1.7" generator="Quartermaster"><pt:body currentSheet="Sheet 1" direction="LTR">{sheets}</pt:body></pt:document>"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prop = f"""<?xml version="1.0" encoding="UTF-8"?><meta:properties xmlns:meta="http://schemas.brother.info/ptouch/2007/lbx/meta" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"><meta:appName>Quartermaster</meta:appName><dc:title></dc:title><dc:subject></dc:subject><dc:creator>Quartermaster</dc:creator><meta:keyword></meta:keyword><dc:description></dc:description><meta:template></meta:template><dcterms:created>{now}</dcterms:created><dcterms:modified>{now}</dcterms:modified><meta:lastPrinted></meta:lastPrinted><meta:modifiedBy>Quartermaster</meta:modifiedBy><meta:revision>1</meta:revision><meta:editTime>0</meta:editTime><meta:numPages>0</meta:numPages><meta:numWords>0</meta:numWords><meta:numChars>0</meta:numChars><meta:security>0</meta:security><meta:transferScript></meta:transferScript></meta:properties>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("label.xml", label)
        z.writestr("prop.xml", prop)
    return buf.getvalue()


def file_stem(row: dict) -> str:
    text = " ".join([row["code"]] + [x for x in text_lines(row) if x != row["code"]])
    return re.sub(r"[^A-Za-z0-9 ._-]+", "", text).strip()[:120] or row["code"]
