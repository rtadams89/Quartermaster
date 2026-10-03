"""Product list as CSV: export it, edit it in a spreadsheet, import it back (or import a new one)."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import security
from ..codes import normalize_code
from ..db import get_db
from ..models import Barcode, Caliber, Product
from ..services import brand_spellings, fmt_weight, minimums, set_minimum

router = APIRouter(prefix="/api", dependencies=[Depends(security.require_auth)])

COLUMNS = ["caliber", "manufacturer", "name", "weight_gr", "type", "rounds_per_box", "cost_per_box",
           "low_stock_rounds", "codes", "notes"]
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_ERRORS = 50


@router.get("/export/products.csv")
def export_products(db: Session = Depends(get_db)):
    mins = minimums(db)
    rows = []
    for p in db.scalars(select(Product)):
        rows.append([
            p.caliber.name, p.brand, p.name, fmt_weight(p.bullet_weight_gr) if p.bullet_weight_gr else "",
            p.bullet_type, p.rounds_per_box, "" if p.cost_per_box is None else f"{p.cost_per_box:.2f}",
            mins.get(("product", p.id), ""), " ".join(sorted(b.code for b in p.barcodes)), p.notes,
        ])
    rows.sort(key=lambda r: (r[0].lower(), float(r[3] or 0), str(r[1]).lower(), str(r[2]).lower()))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="quartermaster-products.csv"'})


# ------------------------------------------------------------------- import
class RowError(Exception):
    pass


def _num(text: str, kind, lo, hi, what: str):
    try:
        v = kind(text)
    except ValueError:
        raise RowError(f"{what} must be a number")
    if not lo <= v <= hi:
        raise RowError(f"{what} must be between {lo} and {hi}")
    return v


def _parse_row(raw: dict, present: set[str]) -> dict:
    """Validate one CSV row. Returns the fields to apply (only columns that are in the file)."""
    g = {k: (raw.get(k) or "").strip() for k in COLUMNS}
    caliber = g["caliber"]
    if not caliber or len(caliber) > 64:
        raise RowError("caliber is required (64 characters at most)")
    out: dict = {"caliber": caliber}
    for col, field, limit in (("manufacturer", "brand", 80), ("name", "name", 120), ("type", "bullet_type", 40)):
        if col in present:
            if len(g[col]) > limit:
                raise RowError(f"{col} is too long ({limit} characters at most)")
            out[field] = g[col]
    if "notes" in present:
        out["notes"] = g["notes"]
    out["rounds_per_box"] = _num(g["rounds_per_box"], int, 1, 10000, "rounds_per_box")
    if "weight_gr" in present:
        out["bullet_weight_gr"] = _num(g["weight_gr"], float, 0.01, 5000, "weight_gr") if g["weight_gr"] else None
    if "cost_per_box" in present:
        cost = g["cost_per_box"].replace("$", "").replace(",", "").strip()  # "$1,234.50" is fine
        out["cost_per_box"] = round(_num(cost, float, 0, 1e9, "cost_per_box (US dollars)"), 2) if cost else None
    if "low_stock_rounds" in present:
        out["min_rounds"] = _num(g["low_stock_rounds"], int, 0, 1_000_000, "low_stock_rounds") if g["low_stock_rounds"] else None
    codes = []
    if "codes" in present:
        for c in g["codes"].replace(";", " ").replace(",", " ").split():
            try:
                codes.append(normalize_code(c))
            except ValueError:
                raise RowError(f"'{c}' is not a valid barcode")
        out["codes"] = list(dict.fromkeys(codes))
    return out


def _read(body: bytes) -> tuple[list[dict], set[str], list[str]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = body.decode("cp1252", errors="replace")  # a spreadsheet's plain "CSV" export
    reader = csv.DictReader(io.StringIO(text))
    names = [(n or "").strip().lower() for n in (reader.fieldnames or [])]
    present = {n for n in names if n in COLUMNS}
    if not {"caliber", "rounds_per_box"} <= present:
        raise HTTPException(400, "The first row must have column names, including at least: caliber, rounds_per_box")
    ignored = [n for n in names if n and n not in COLUMNS]
    rows = []
    for raw in reader:
        row = {(k or "").strip().lower(): v for k, v in raw.items() if k}
        if any((v or "").strip() for v in row.values() if isinstance(v, str)):
            rows.append(row)
        if len(rows) > MAX_ROWS:
            raise HTTPException(400, f"That file has more than {MAX_ROWS} rows")
    return rows, present, ignored


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


@router.post("/import/products")
async def import_products(request: Request, apply: bool = False, db: Session = Depends(get_db)):
    """Body is the raw CSV. Without apply=true nothing changes: it only reports what would happen.
    A file with any problem rows is refused as a whole, so an import never half-applies."""
    body = await request.body()
    if not body or len(body) > MAX_BYTES:
        raise HTTPException(400, "Send a CSV file under 2 MB")
    rows, present, ignored = _read(body)

    calibers = {c.name.lower(): c for c in db.scalars(select(Caliber))}
    products = list(db.scalars(select(Product)))
    code_owner = {b.code: b.product_id for b in db.scalars(select(Barcode)) if b.product_id is not None}
    mins = minimums(db)
    brands = brand_spellings(db)  # a brand typed "federal" in the file becomes the "Federal" already on file

    errors: list[dict] = []
    plan: list[tuple[Product | None, dict]] = []
    seen_products: dict[int, int] = {}
    seen_new: dict[tuple, int] = {}
    seen_codes: dict[str, int] = {}
    for n, raw in enumerate(rows, start=2):  # row 1 is the header
        try:
            data = _parse_row(raw, present)
            if data.get("brand"):
                data["brand"] = brands.setdefault(data["brand"].lower(), data["brand"])
            owners = {code_owner[c] for c in data.get("codes", []) if c in code_owner}
            if len(owners) > 1:
                raise RowError("its barcodes already belong to different products")
            for c in data.get("codes", []):
                if c in seen_codes:
                    raise RowError(f"barcode {c} is also on row {seen_codes[c]}")
            match = None
            if owners:
                match = next(p for p in products if p.id in owners)
            else:
                cal = calibers.get(data["caliber"].lower())
                for p in products:
                    if (cal and p.caliber_id == cal.id and _same(p.brand, data.get("brand", p.brand))
                            and _same(p.name, data.get("name", p.name))
                            and _same(p.bullet_type, data.get("bullet_type", p.bullet_type))
                            and (p.bullet_weight_gr or None) == data.get("bullet_weight_gr", p.bullet_weight_gr or None)
                            and p.rounds_per_box == data["rounds_per_box"]):
                        match = p
                        break
            if match is not None:
                if match.id in seen_products:
                    raise RowError(f"it is the same product as row {seen_products[match.id]}")
                seen_products[match.id] = n
                for c in data.get("codes", []):
                    if code_owner.get(c, match.id) != match.id:
                        raise RowError(f"barcode {c} belongs to another product")
            if match is None:
                key = (data["caliber"].lower(), data.get("brand", "").lower(), data.get("name", "").lower(),
                       data.get("bullet_weight_gr"), data.get("bullet_type", "").lower(), data["rounds_per_box"])
                if key in seen_new:
                    raise RowError(f"it is the same new product as row {seen_new[key]}; put all its barcodes on one row")
                seen_new[key] = n
            for c in data.get("codes", []):
                seen_codes[c] = n
            plan.append((match, data))
        except RowError as e:
            errors.append({"row": n, "error": str(e)})

    def changed(p: Product, d: dict) -> bool:
        cal = calibers.get(d["caliber"].lower())
        if cal is None or cal.id != p.caliber_id:
            return True
        for k in ("brand", "name", "bullet_type", "notes", "bullet_weight_gr", "cost_per_box", "rounds_per_box"):
            if k in d and d[k] != getattr(p, k):
                return True
        if "min_rounds" in d and (d["min_rounds"] or None) != mins.get(("product", p.id)):
            return True
        have = {b.code for b in p.barcodes}
        return any(c not in have for c in d.get("codes", []))

    new_calibers = sorted({d["caliber"] for _, d in plan if d["caliber"].lower() not in calibers}, key=str.lower)
    creates = sum(1 for m, _ in plan if m is None)
    updates = sum(1 for m, d in plan if m is not None and changed(m, d))
    summary = {
        "rows": len(rows), "create": creates, "update": updates,
        "unchanged": len(plan) - creates - updates, "new_calibers": new_calibers,
        "ignored_columns": ignored, "errors": errors[:MAX_ERRORS], "error_count": len(errors),
        "applied": False,
    }
    if not apply:
        return summary
    if errors:
        raise HTTPException(400, f"{len(errors)} row(s) have problems; nothing was imported")

    top = db.scalar(select(func.coalesce(func.max(Caliber.sort_order), 0))) or 0
    for name in new_calibers:
        top += 10
        c = Caliber(name=name, sort_order=top)
        db.add(c)
        calibers[name.lower()] = c
    db.flush()
    for match, d in plan:
        fields = {k: v for k, v in d.items() if k not in ("caliber", "codes", "min_rounds")}
        fields["caliber_id"] = calibers[d["caliber"].lower()].id
        if match is None:
            match = Product(**fields)
            db.add(match)
            db.flush()
        else:
            for k, v in fields.items():
                setattr(match, k, v)
        if "min_rounds" in d:
            set_minimum(db, "product", match.id, d["min_rounds"])
        for c in d.get("codes", []):
            bc = db.get(Barcode, c)
            if bc is None:
                bc = Barcode(code=c)
                db.add(bc)
            bc.product_id = match.id
    db.commit()
    summary["applied"] = True
    return summary
