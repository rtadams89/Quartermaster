"""Inventory maths shared by the routers."""
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import iso
from .models import Barcode, BarcodePhoto, Caliber, Product, StockMinimum, Transaction

UNIDENTIFIED = "unidentified"
NO_BRAND = "(none)"  # drill-down key for products with no manufacturer


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
        "weight_na": p.bullet_weight_gr == 0,  # 0 grains is how "N/A" (no traditional bullet weight) is stored
        "bullet_type": p.bullet_type,
        "rounds_per_box": p.rounds_per_box,
        "single": p.rounds_per_box == 1,  # counted by the individual round, not by the box
        "cost_per_box": p.cost_per_box,
        "cost_per_round": round(p.cost_per_box / p.rounds_per_box, 4) if p.cost_per_box is not None else None,
        "notes": p.notes,
        "indoor_safe": bool(p.indoor_safe),
    }


def price_range(items: list[dict]) -> dict | None:
    """Lowest and highest cost per round among items that are in stock and have a cost, else None."""
    costs = [i["cost_per_round"] for i in items if i["boxes"] > 0 and i.get("cost_per_round") is not None]
    return {"low": min(costs), "high": max(costs)} if costs else None


def is_shotshell(caliber_name: str | None) -> bool:
    """12ga, 20 gauge, .410 and so on: shot and slugs are described by weight in ounces, not bullet grains."""
    return bool(re.search(r"\d\s*ga\b|gauge|\b410\b", caliber_name or "", re.I))


def missing_details(p: Product) -> list[str]:
    """Plain-language list of the details a product still lacks (empty when it is complete)."""
    missing = []
    if p.cost_per_box is None:
        missing.append("cost")
    if not (p.brand or "").strip():
        missing.append("manufacturer")
    if not (p.bullet_type or "").strip():
        missing.append("bullet type")
    if p.bullet_weight_gr is None and not is_shotshell(p.caliber.name if p.caliber else ""):  # 0 = N/A, which is an answer
        missing.append("bullet weight")
    return missing


def out_of_stock_calibers(db: Session, items: list[dict] | None = None) -> list[Caliber]:
    """Active calibers with nothing on hand that you do keep (they have a product, or an alert level).
    A caliber that was never used does not count as out of stock."""
    if items is None:
        items, _ = inventory_by_product(db)
    stocked = {i["caliber_id"] for i in items if i["boxes"] > 0}
    kept = set(db.scalars(select(Product.caliber_id))) | {rid for (kind, rid) in minimums(db) if kind == "caliber"}
    return [
        c for c in db.scalars(select(Caliber).where(Caliber.active.is_(True)).order_by(Caliber.sort_order, Caliber.name))
        if c.id not in stocked and c.id in kept
    ]


def stock_money(items: list[dict]) -> dict:
    """What the in-stock boxes cost in total (boxes x cost per box), and how many in-stock products
    have no cost entered and so are left out of that total."""
    live = [i for i in items if i["boxes"] > 0]
    return {
        "value": round(sum(i["boxes"] * i["cost_per_box"] for i in live if i["cost_per_box"] is not None), 2),
        "unpriced": sum(1 for i in live if i["cost_per_box"] is None),
    }


def box_count(items: list[dict]) -> int:
    """Boxes among these items. Products counted by the single round (1 round per box) have no boxes
    to count; their rounds still show in the rounds totals."""
    return sum(i["boxes"] for i in items if i["rounds_per_box"] != 1)


def spec_text(p: Product) -> str:
    """e.g. '115 gr FMJ · 50 rd/box'"""
    bits = []
    if p.bullet_weight_gr:
        bits.append(f"{fmt_weight(p.bullet_weight_gr)} gr")
    if p.bullet_type:
        bits.append(p.bullet_type)
    spec = " ".join(bits)
    box = "by the round" if p.rounds_per_box == 1 else f"{p.rounds_per_box} rd/box"
    text = f"{spec} · {box}" if spec else box
    return text if p.indoor_safe else f"{text} · outdoor range only"


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


def drill(db: Session, caliber: str | None, weight: str | None, manufacturer: str | None = None,
          by_manufacturer: bool = False, include_empty: bool = False) -> dict:
    """Drill-down: caliber -> bullet weight -> product (the kiosk), or with by_manufacturer
    caliber -> bullet weight -> manufacturer -> product (the admin site)."""
    items, unidentified = inventory_by_product(db)
    items = [i for i in items if i["boxes"] != 0]
    unid = [u for u in unidentified if u["boxes"] != 0]

    calibers = list(db.scalars(select(Caliber).order_by(Caliber.sort_order, Caliber.name)))
    mins = minimums(db)
    # include_empty (admin site): also list the calibers you keep but have run out of
    gone = {c.id for c in out_of_stock_calibers(db)} if include_empty else set()

    if caliber is None:
        rows = []
        for c in calibers:
            mine = [i for i in items if i["caliber_id"] == c.id]
            floor = mins.get(("caliber", c.id))
            if mine or floor or c.id in gone:  # a caliber with an alert level stays listed even when it runs out
                rounds = sum(i["rounds"] for i in mine)
                rows.append(
                    {
                        "key": str(c.id),
                        "label": c.name,
                        "boxes": box_count(mine),
                        "rounds": rounds,
                        "drillable": bool(mine),
                        "out": not any(i["boxes"] > 0 for i in mine),
                        "low": bool(floor) and rounds < floor,
                        "price": price_range(mine),
                        **stock_money(mine),
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
                    "value": 0,
                    "unpriced": 0,
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
                "value": 0,
                "unpriced": 0,
                "item": u,
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
        for key in sorted(groups, key=lambda k: ({"na": 1, "none": 2}.get(k, 0), float(k) if k not in ("na", "none") else 0)):
            g = groups[key]
            rows.append(
                {
                    "key": key,
                    "label": _wlabel(key),
                    "boxes": box_count(g),
                    "rounds": sum(i["rounds"] for i in g),
                    "drillable": True,
                    "price": price_range(g),
                    **stock_money(g),
                }
            )
        return _drill_result("weight", crumbs, rows)

    mine = [i for i in mine if _wkey(i["bullet_weight_gr"]) == weight]
    crumbs.append(_wlabel(weight))

    if by_manufacturer:
        if manufacturer is None:
            groups = {}
            for i in mine:
                groups.setdefault(_mkey(i["brand"]), []).append(i)
            rows = [
                {
                    "key": key,
                    "label": "No manufacturer" if key == NO_BRAND else key,
                    "boxes": box_count(g),
                    "rounds": sum(i["rounds"] for i in g),
                    "drillable": True,
                    "price": price_range(g),
                    **stock_money(g),
                }
                for key, g in sorted(groups.items(), key=lambda kv: (kv[0] == NO_BRAND, kv[0].lower()))
            ]
            return _drill_result("manufacturer", crumbs, rows)
        mine = [i for i in mine if _mkey(i["brand"]) == manufacturer]
        crumbs.append("No manufacturer" if manufacturer == NO_BRAND else manufacturer)

    rows = [
        {
            "key": str(i["id"]),
            "label": i["label"],
            "sublabel": i["spec"] + (" · " + ", ".join(c["code"] for c in i["codes"]) if i["codes"] else ""),
            "boxes": i["boxes"],
            "rounds": i["rounds"],
            "drillable": False,
            "single": i["rounds_per_box"] == 1,
            "low": i["low"],
            "price": price_range([i]),
            **stock_money([i]),
            "item": i,
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
                "boxes": i["boxes"], "rounds": i["rounds"], "code_boxes": mine["boxes"], "single": i["rounds_per_box"] == 1,
            }
    for u in unidentified:
        if u["code"] == code:
            return {
                "found": True, "code": code, "identified": False, "label": code, "spec": "Unidentified code",
                "caliber": UNIDENTIFIED, "weight": None, "row": code,
                "boxes": u["boxes"], "rounds": None, "code_boxes": u["boxes"],
            }
    return {"found": False, "code": code}


def _mkey(brand: str | None) -> str:
    return (brand or "").strip() or NO_BRAND


def _wkey(w: float | None) -> str:
    """Drill-down key for a bullet weight: the grains, "na" (no traditional weight) or "none" (not entered)."""
    return "none" if w is None else "na" if w == 0 else fmt_weight(w)


def _wlabel(key: str) -> str:
    return "No weight listed" if key == "none" else "N/A" if key == "na" else f"{key} gr"


def _drill_result(level: str, crumbs: list[str], rows: list[dict]) -> dict:
    return {
        "level": level,
        "breadcrumb": crumbs,
        "total_boxes": sum(r["boxes"] for r in rows if not r.get("single")),
        "total_rounds": sum(r["rounds"] or 0 for r in rows),
        "has_unknown_rounds": any(r["rounds"] is None for r in rows),
        "total_value": round(sum(r.get("value") or 0 for r in rows), 2),
        "unpriced": sum(r.get("unpriced") or 0 for r in rows),
        "rows": rows,
    }
