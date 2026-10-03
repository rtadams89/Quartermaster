"""Inventory maths shared by the routers."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import iso
from .models import Barcode, BarcodePhoto, Caliber, Product, StockMinimum, Transaction

UNIDENTIFIED = "unidentified"


def photo_codes(db: Session) -> set[str]:
    """Codes that have a box photo."""
    return set(db.scalars(select(BarcodePhoto.code)))


def brand_spellings(db: Session, skip_id: int | None = None) -> dict[str, str]:
    """Every brand already in use, as {lower-case: spelling}. If one brand is spelled two ways,
    the spelling on more products wins (ties go to the one that sorts first)."""
    counts: dict[str, int] = {}
    for b in db.scalars(select(Product.brand).where(Product.id != skip_id) if skip_id else select(Product.brand)):
        b = (b or "").strip()
        if b:
            counts[b] = counts.get(b, 0) + 1
    best: dict[str, str] = {}
    for b in sorted(counts, key=lambda x: (-counts[x], x)):
        best.setdefault(b.lower(), b)
    return best


def canonical_brand(db: Session, brand: str, skip_id: int | None = None) -> str:
    """Trim a typed brand and reuse the spelling other products already use ("federal" becomes
    "Federal"). skip_id is the product being edited, so a lone brand can still be re-cased."""
    brand = (brand or "").strip()
    return brand_spellings(db, skip_id).get(brand.lower(), brand) if brand else ""


def minimums(db: Session) -> dict[tuple[str, int], int]:
    return {(m.kind, m.ref_id): m.min_rounds for m in db.scalars(select(StockMinimum))}


def set_minimum(db: Session, kind: str, ref_id: int, value: int | None) -> None:
    """Set (or, with None/0, clear) a low-stock level. The caller commits."""
    row = db.get(StockMinimum, (kind, ref_id))
    if not value:
        if row:
            db.delete(row)
    elif row:
        row.min_rounds = value
    else:
        db.add(StockMinimum(kind=kind, ref_id=ref_id, min_rounds=value))


def product_dict(p: Product | None) -> dict | None:
    if p is None:
        return None
    return {
        "id": p.id,
        "caliber_id": p.caliber_id,
        "caliber": p.caliber.name if p.caliber else None,
        "brand": p.brand,
        "name": p.name,
        "label": " ".join(x for x in (p.brand, p.name) if x) or "(unnamed)",
        "bullet_weight_gr": p.bullet_weight_gr,
        "bullet_type": p.bullet_type,
        "rounds_per_box": p.rounds_per_box,
        "cost_per_box": p.cost_per_box,
        "notes": p.notes,
    }


def spec_text(p: Product) -> str:
    """e.g. '115 gr FMJ · 50 rd/box'"""
    bits = []
    if p.bullet_weight_gr:
        bits.append(f"{fmt_weight(p.bullet_weight_gr)} gr")
    if p.bullet_type:
        bits.append(p.bullet_type)
    spec = " ".join(bits)
    box = f"{p.rounds_per_box} rd/box"
    return f"{spec} · {box}" if spec else box


def fmt_weight(w: float) -> str:
    return str(int(w)) if float(w).is_integer() else f"{w:g}"


def on_hand_by_code(db: Session) -> dict[str, int]:
    rows = db.execute(select(Transaction.code, func.sum(Transaction.boxes)).group_by(Transaction.code))
    return {code: int(total or 0) for code, total in rows}


def on_hand(db: Session, code: str) -> int:
    return int(
        db.scalar(select(func.coalesce(func.sum(Transaction.boxes), 0)).where(Transaction.code == code))
        or 0
    )


def activity_by_code(db: Session) -> dict[str, dict]:
    rows = db.execute(
        select(Transaction.code, func.count(Transaction.id), func.max(Transaction.ts)).group_by(
            Transaction.code
        )
    )
    return {c: {"count": n, "last": iso(last)} for c, n, last in rows}


def all_barcodes(db: Session) -> list[Barcode]:
    return list(db.scalars(select(Barcode)))


def inventory_by_product(db: Session) -> tuple[list[dict], list[dict]]:
    """Return (products_with_stock_info, unidentified_codes).

    Every product is included (even at zero stock); the callers filter.
    """
    stock = on_hand_by_code(db)
    act = activity_by_code(db)
    photos = photo_codes(db)
    mins = minimums(db)
    products = {p.id: p for p in db.scalars(select(Product))}
    entries: dict[int, dict] = {}
    for pid, p in products.items():
        entries[pid] = {
            **product_dict(p),
            "spec": spec_text(p),
            "codes": [],
            "photo_code": None,
            "boxes": 0,
            "rounds": 0,
            "last_activity": None,
            "min_rounds": mins.get(("product", pid)),
            "low": False,
        }
    unidentified: list[dict] = []
    for bc in all_barcodes(db):
        boxes = stock.get(bc.code, 0)
        a = act.get(bc.code, {"count": 0, "last": None})
        if bc.product_id is None:
            unidentified.append(
                {
                    "code": bc.code,
                    "boxes": boxes,
                    "transactions": a["count"],
                    "last_activity": a["last"],
                    "first_seen_at": iso(bc.first_seen_at),
                    "has_photo": bc.code in photos,
                }
            )
            continue
        e = entries[bc.product_id]
        e["codes"].append({"code": bc.code, "boxes": boxes, "has_photo": bc.code in photos})
        if bc.code in photos and e["photo_code"] is None:
            e["photo_code"] = bc.code
        e["boxes"] += boxes
        e["rounds"] += boxes * products[bc.product_id].rounds_per_box
        if a["last"] and (e["last_activity"] is None or a["last"] > e["last_activity"]):
            e["last_activity"] = a["last"]
    for e in entries.values():
        e["low"] = bool(e["min_rounds"]) and e["rounds"] < e["min_rounds"]
    return list(entries.values()), unidentified


def low_stock(db: Session) -> dict:
    """Calibers and products whose rounds on hand are below the level set for them."""
    items, _ = inventory_by_product(db)
    mins = minimums(db)
    by_caliber: dict[int, int] = {}
    for i in items:
        by_caliber[i["caliber_id"]] = by_caliber.get(i["caliber_id"], 0) + i["rounds"]
    calibers = [
        {"id": c.id, "name": c.name, "rounds": by_caliber.get(c.id, 0), "min_rounds": mins[("caliber", c.id)]}
        for c in db.scalars(select(Caliber).order_by(Caliber.sort_order, Caliber.name))
        if ("caliber", c.id) in mins and by_caliber.get(c.id, 0) < mins[("caliber", c.id)]
    ]
    products = [
        {"id": i["id"], "label": i["label"], "caliber": i["caliber"], "rounds": i["rounds"], "min_rounds": i["min_rounds"]}
        for i in sorted(items, key=lambda i: ((i["caliber"] or "").lower(), i["label"].lower()))
        if i["low"]
    ]
    return {"calibers": calibers, "products": products, "count": len(calibers) + len(products)}


def drill(db: Session, caliber: str | None, weight: str | None) -> dict:
    """Drill-down for the kiosk: caliber -> bullet weight -> product."""
    items, unidentified = inventory_by_product(db)
    items = [i for i in items if i["boxes"] != 0]
    unid = [u for u in unidentified if u["boxes"] != 0]

    calibers = list(db.scalars(select(Caliber).order_by(Caliber.sort_order, Caliber.name)))
    mins = minimums(db)

    if caliber is None:
        rows = []
        for c in calibers:
            mine = [i for i in items if i["caliber_id"] == c.id]
            floor = mins.get(("caliber", c.id))
            if mine or floor:  # a caliber with an alert level stays listed even when it runs out
                rounds = sum(i["rounds"] for i in mine)
                rows.append(
                    {
                        "key": str(c.id),
                        "label": c.name,
                        "boxes": sum(i["boxes"] for i in mine),
                        "rounds": rounds,
                        "drillable": bool(mine),
                        "low": bool(floor) and rounds < floor,
                    }
                )
        if unid:
            rows.append(
                {
                    "key": UNIDENTIFIED,
                    "label": "Unidentified",
                    "sublabel": "Not yet described in the admin site",
                    "boxes": sum(u["boxes"] for u in unid),
                    "rounds": None,
                    "drillable": True,
                }
            )
        return _drill_result("caliber", [], rows)

    if caliber == UNIDENTIFIED:
        rows = [
            {
                "key": u["code"],
                "label": u["code"],
                "sublabel": "Unidentified code",
                "boxes": u["boxes"],
                "rounds": None,
                "drillable": False,
            }
            for u in sorted(unid, key=lambda u: u["code"])
        ]
        return _drill_result("product", ["Unidentified"], rows)

    cal = next((c for c in calibers if str(c.id) == caliber), None)
    crumbs = [cal.name if cal else "?"]
    mine = [i for i in items if str(i["caliber_id"]) == caliber]

    if weight is None:
        groups: dict[str, list[dict]] = {}
        for i in mine:
            groups.setdefault(_wkey(i["bullet_weight_gr"]), []).append(i)
        rows = []
        for key in sorted(groups, key=lambda k: (k == "none", float(k) if k != "none" else 0)):
            g = groups[key]
            rows.append(
                {
                    "key": key,
                    "label": "No weight listed" if key == "none" else f"{key} gr",
                    "boxes": sum(i["boxes"] for i in g),
                    "rounds": sum(i["rounds"] for i in g),
                    "drillable": True,
                }
            )
        return _drill_result("weight", crumbs, rows)

    mine = [i for i in mine if _wkey(i["bullet_weight_gr"]) == weight]
    crumbs.append("No weight listed" if weight == "none" else f"{weight} gr")
    rows = [
        {
            "key": str(i["id"]),
            "label": i["label"],
            "sublabel": i["spec"] + (" · " + ", ".join(c["code"] for c in i["codes"]) if i["codes"] else ""),
            "boxes": i["boxes"],
            "rounds": i["rounds"],
            "drillable": False,
            "low": i["low"],
        }
        for i in sorted(mine, key=lambda i: i["label"].lower())
    ]
    return _drill_result("product", crumbs, rows)


def locate(db: Session, code: str) -> dict:
    """Where a scanned code sits in the kiosk drill-down, and how much of it is in stock."""
    items, unidentified = inventory_by_product(db)
    for i in items:
        mine = next((c for c in i["codes"] if c["code"] == code), None)
        if mine:
            return {
                "found": True, "code": code, "identified": True, "label": i["label"], "spec": i["spec"],
                "caliber": str(i["caliber_id"]), "weight": _wkey(i["bullet_weight_gr"]), "row": str(i["id"]),
                "boxes": i["boxes"], "rounds": i["rounds"], "code_boxes": mine["boxes"],
            }
    for u in unidentified:
        if u["code"] == code:
            return {
                "found": True, "code": code, "identified": False, "label": code, "spec": "Unidentified code",
                "caliber": UNIDENTIFIED, "weight": None, "row": code,
                "boxes": u["boxes"], "rounds": None, "code_boxes": u["boxes"],
            }
    return {"found": False, "code": code}


def _wkey(w: float | None) -> str:
    return "none" if not w else fmt_weight(w)


def _drill_result(level: str, crumbs: list[str], rows: list[dict]) -> dict:
    return {
        "level": level,
        "breadcrumb": crumbs,
        "total_boxes": sum(r["boxes"] for r in rows),
        "total_rounds": sum(r["rounds"] or 0 for r in rows),
        "has_unknown_rounds": any(r["rounds"] is None for r in rows),
        "rows": rows,
    }
