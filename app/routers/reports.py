from collections import defaultdict
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import security
from ..db import get_db, utcnow
from ..models import Barcode, Product, Transaction
from ..services import product_dict

router = APIRouter(prefix="/api/reports", dependencies=[Depends(security.require_auth)])

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return timezone.utc


def _months(start, end):
    y, m = start
    while (y, m) <= end:
        yield (y, m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _sum_cols(rows, field):
    if not rows:
        return []
    return [None if any(r[field][i] is None for r in rows) else round(sum(r[field][i] for r in rows), 2)
            for i in range(len(rows[0][field]))]


def _row(key, label, out, inn, cost, onhand, counted, onhand_now):
    return {"key": key, "label": label, "out": out, "in": inn, "cost": cost, "onhand": onhand,
            "out_total": sum(out), "in_total": sum(inn), "cost_total": round(sum(cost), 2),
            "avg_out": round(sum(out) / counted) if counted else None, "onhand_now": onhand_now}


@router.get("/usage")
def usage(year: int | None = Query(default=None, ge=2000, le=2200), caliber_id: int | None = None,
          tz: str = "UTC", db: Session = Depends(get_db)):
    zone = _zone(tz)
    now = utcnow().replace(tzinfo=timezone.utc).astimezone(zone)
    this_month = (now.year, now.month)
    by_product = caliber_id is not None

    rows = db.execute(
        select(Transaction.ts, Transaction.boxes, Transaction.kind, Product)
        .join(Barcode, Barcode.code == Transaction.code, isouter=True)
        .join(Product, Product.id == Barcode.product_id, isouter=True)
        .order_by(Transaction.id)
    ).all()

    cells = defaultdict(lambda: {"out": 0, "in": 0, "net": 0, "cost": 0.0})
    names, unpriced, unidentified, first = {}, set(), 0, None
    for ts, boxes, kind, p in rows:
        local = ts.replace(tzinfo=timezone.utc).astimezone(zone)
        month = (local.year, local.month)
        in_year = year is None or local.year == year
        if p is None:
            if kind == "out" and in_year:
                unidentified += -boxes
            continue
        if by_product and p.caliber_id != caliber_id:
            continue
        first = month if first is None or month < first else first
        key = p.id if by_product else p.caliber_id
        names[key] = product_dict(p)["label"] if by_product else p.caliber.name
        rounds = boxes * p.rounds_per_box
        c = cells[(key, month)]
        c["net"] += rounds
        if kind == "out":
            c["out"] += -rounds
            if p.cost_per_box is None:
                if in_year:
                    unpriced.add(p.id)
            else:
                c["cost"] += -rounds * p.cost_per_box / p.rounds_per_box
        elif kind == "in":
            c["in"] += rounds

    years = sorted({m[0] for _, m in cells} | {this_month[0]})
    if year is None:
        periods = [(y, None) for y in years]
        columns = [{"key": str(y), "label": str(y), "future": False} for y in years]
    else:
        periods = [(year, m) for m in range(1, 13)]
        columns = [{"key": f"{year}-{m:02d}", "label": MONTHS[m - 1], "future": (year, m) > this_month}
                   for m in range(1, 13)]

    def in_period(month, period):
        return month[0] == period[0] and (period[1] is None or month[1] == period[1])

    counted = 0
    if first:
        counted = sum(1 for m in _months(first, this_month) if any(in_period(m, p) for p in periods))

    result = []
    for key in sorted(names, key=lambda k: names[k].lower()):
        months = sorted(m for k, m in cells if k == key)
        out, inn, cost, onhand = [], [], [], []
        running = idx = 0
        for period, col in zip(periods, columns):
            end = (period[0], period[1] or 12)
            while idx < len(months) and months[idx] <= end:
                running += cells[(key, months[idx])]["net"]
                idx += 1
            mine = [cells[(key, m)] for m in months if in_period(m, period)]
            out.append(sum(c["out"] for c in mine))
            inn.append(sum(c["in"] for c in mine))
            cost.append(round(sum(c["cost"] for c in mine), 2))
            onhand.append(None if col["future"] else running)
        if not any(out) and not any(inn):
            continue
        result.append(_row(key, names[key], out, inn, cost, onhand, counted,
                           sum(cells[(key, m)]["net"] for m in months)))

    total = _row(None, "Total", *(_sum_cols(result, f) for f in ("out", "in", "cost", "onhand")), counted,
                 sum(r["onhand_now"] for r in result))
    return {"years": years, "year": year, "columns": columns, "by": "product" if by_product else "caliber",
            "rows": result, "total": total, "months_counted": counted,
            "unidentified_boxes": unidentified, "unpriced": len(unpriced)}
