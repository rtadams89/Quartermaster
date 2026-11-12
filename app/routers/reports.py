import calendar
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta, timezone
from itertools import accumulate
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


@router.get("/onhand")
def onhand(year: int = Query(ge=2000, le=2200), month: int | None = Query(default=None, ge=1, le=12),
           caliber_id: int | None = None, tz: str = "UTC", db: Session = Depends(get_db)):
    zone = _zone(tz)
    today = utcnow().replace(tzinfo=timezone.utc).astimezone(zone).date()
    by_product = caliber_id is not None

    rows = db.execute(
        select(Transaction.ts, Transaction.boxes, Product)
        .join(Barcode, Barcode.code == Transaction.code)
        .join(Product, Product.id == Barcode.product_id)
        .order_by(Transaction.id)
    ).all()

    net, names = defaultdict(lambda: defaultdict(int)), {}
    for ts, boxes, p in rows:
        if by_product and p.caliber_id != caliber_id:
            continue
        key = p.id if by_product else p.caliber_id
        names[key] = product_dict(p)["label"] if by_product else p.caliber.name
        net[key][ts.replace(tzinfo=timezone.utc).astimezone(zone).date()] += boxes * p.rounds_per_box

    if month:
        days = calendar.monthrange(year, month)[1]
        ends = [date(year, month, d) for d in range(1, days + 1)]
        labels = [str(d) for d in range(1, days + 1)]
    else:
        ends = [date(year, m, calendar.monthrange(year, m)[1]) for m in range(1, 13)]
        labels = MONTHS
    starts = ends if month else [date(year, m, 1) for m in range(1, 13)]
    future = [st > today for st in starts]
    ends = [min(e, today) for e in ends]

    series = []
    for key in sorted(names, key=lambda k: names[k].lower()):
        dates = sorted(net[key])
        running = list(accumulate(net[key][d] for d in dates))
        values = [None if f else (running[bisect_right(dates, e) - 1] if bisect_right(dates, e) else 0)
                  for e, f in zip(ends, future)]
        series.append({"key": key, "label": names[key], "values": values, "now": running[-1] if running else 0})

    series = [s for s in series if s["now"] or any(v for v in s["values"] if v)]
    total = [None if f else sum(s["values"][i] for s in series) for i, f in enumerate(future)]
    first_year = min((d.year for k in net for d in net[k]), default=today.year)
    return {"year": year, "month": month, "by": "product" if by_product else "caliber",
            "labels": labels, "series": series, "total": total,
            "now": sum(s["now"] for s in series), "years": list(range(today.year, min(first_year, today.year) - 1, -1))}
