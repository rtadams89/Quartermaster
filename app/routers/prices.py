"""Look up current market prices on request, review them, and save the ones you accept."""
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import pricing, security
from ..db import get_db, iso, utcnow
from ..models import Caliber, Product, Setting
from ..services import inventory_by_product

router = APIRouter(prefix="/api/prices", dependencies=[Depends(security.require_auth)])

PRICE_KEY = "price_api_key"
RATE_KEY = "price_per_minute"
SLUGS_KEY = "price_caliber_slugs"
OPTIONS_KEY = "price_caliber_options"
WORDS_KEY = "price_caliber_words"
DEFAULT_PER_MINUTE = 5
TAX_KEY = "price_tax_percent"
STALE_DAYS = 90


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def _put(db: Session, key: str, value: str | None) -> None:
    row = db.get(Setting, key)
    if value is None:
        if row:
            db.delete(row)
    elif row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def _json(db: Session, key: str, default):
    try:
        return json.loads(_get(db, key) or "null") or default
    except ValueError:
        return default


def _tax(db: Session) -> float:
    try:
        return max(0.0, min(30.0, float(_get(db, TAX_KEY, "0"))))
    except ValueError:
        return 0.0


def _per_minute(db: Session) -> int:
    try:
        return max(1, min(250, int(_get(db, RATE_KEY, str(DEFAULT_PER_MINUTE)))))
    except ValueError:
        return DEFAULT_PER_MINUTE


def _slugs(db: Session) -> dict[str, str]:
    """Caliber id -> AmmoSeek caliber slug: the saved choice, else the best guess from the cached AmmoSeek list."""
    saved = _json(db, SLUGS_KEY, {})
    options = _json(db, OPTIONS_KEY, [])
    out = {}
    for c in db.scalars(select(Caliber)):
        if str(c.id) in saved:
            if saved[str(c.id)]:
                out[str(c.id)] = saved[str(c.id)]
        elif options:
            guess = pricing.guess_slug([c.group.name if c.group else "", c.name], options)
            if guess:
                out[str(c.id)] = guess
    return out


@router.get("/config")
def config(db: Session = Depends(get_db)):
    slugs, saved, options, words = _slugs(db), _json(db, SLUGS_KEY, {}), _json(db, OPTIONS_KEY, []), _json(db, WORDS_KEY, {})
    labels = {o["slug"]: o["label"] for o in options}
    counts = dict(db.execute(select(Product.caliber_id, func.count(Product.id)).group_by(Product.caliber_id)).all())
    calibers = [{"id": c.id, "name": c.name, "products": counts.get(c.id, 0), "slug": slugs.get(str(c.id)),
                 "label": labels.get(slugs.get(str(c.id)), slugs.get(str(c.id))), "chosen": str(c.id) in saved,
                 "include": words.get(str(c.id), {}).get("include", ""), "exclude": words.get(str(c.id), {}).get("exclude", "")}
                for c in db.scalars(select(Caliber).order_by(Caliber.sort_order, Caliber.name))]
    return {"key_set": bool(_get(db, PRICE_KEY)), "per_minute": _per_minute(db), "tax_percent": _tax(db), "options": options, "calibers": calibers}


class KeyIn(BaseModel):
    key: str = Field(max_length=200)
    per_minute: int = Field(default=DEFAULT_PER_MINUTE, ge=1, le=250)
    tax_percent: float | None = Field(default=None, ge=0, le=30)


@router.put("/settings")
def save_settings(body: KeyIn, db: Session = Depends(get_db)):
    """Save the API key (an empty key leaves the saved one as it is) and the allowed searches per minute."""
    if body.key.strip():
        _put(db, PRICE_KEY, body.key.strip())
    _put(db, RATE_KEY, str(body.per_minute))
    if body.tax_percent is not None:
        _put(db, TAX_KEY, str(body.tax_percent))
    db.commit()
    return {"key_set": bool(_get(db, PRICE_KEY)), "per_minute": body.per_minute, "tax_percent": _tax(db)}


@router.delete("/key")
def clear_key(db: Session = Depends(get_db)):
    _put(db, PRICE_KEY, None)
    db.commit()
    return {"ok": True}


@router.post("/options")
def load_options(db: Session = Depends(get_db)):
    """Fetch AmmoSeek's caliber list and keep it for matching calibers."""
    key = _get(db, PRICE_KEY)
    if not key:
        raise HTTPException(400, "Enter the price service API key first")
    try:
        options = pricing.caliber_options(key)
    except pricing.PriceError as e:
        raise HTTPException(502, str(e))
    _put(db, OPTIONS_KEY, json.dumps(options))
    db.commit()
    return config(db)


class SlugIn(BaseModel):
    slug: str | None = Field(default=None, max_length=80)
    include: str | None = Field(default=None, max_length=80)
    exclude: str | None = Field(default=None, max_length=80)


@router.put("/calibers/{cid}")
def set_slug(cid: int, body: SlugIn, db: Session = Depends(get_db)):
    """Set a caliber's AmmoSeek caliber (an empty choice means none) and its must-include / must-exclude words.
    Only the fields sent are changed."""
    if not db.get(Caliber, cid):
        raise HTTPException(404, "Caliber not found")
    sent = body.model_fields_set
    if "slug" in sent:
        saved = _json(db, SLUGS_KEY, {})
        saved[str(cid)] = (body.slug or "").strip()
        _put(db, SLUGS_KEY, json.dumps(saved))
    if sent & {"include", "exclude"}:
        words = _json(db, WORDS_KEY, {})
        entry = {"include": " ".join((body.include if "include" in sent else words.get(str(cid), {}).get("include", "")).split()),
                 "exclude": " ".join((body.exclude if "exclude" in sent else words.get(str(cid), {}).get("exclude", "")).split())}
        if entry["include"] or entry["exclude"]:
            words[str(cid)] = entry
        else:
            words.pop(str(cid), None)
        _put(db, WORDS_KEY, json.dumps(words))
    db.commit()
    return {"ok": True}


class ScopeIn(BaseModel):
    scope: str = Field(pattern="^(product|caliber|all|stale)$")
    id: int | None = None
    in_stock_only: bool = True
    ids: list[int] | None = Field(default=None, max_length=5000)


def _products(db: Session, body: ScopeIn) -> list[dict]:
    slugs, words = _slugs(db), _json(db, WORDS_KEY, {})
    rows, _ = inventory_by_product(db)
    updated = dict(db.execute(select(Product.id, Product.price_updated)).all())
    cutoff = utcnow() - timedelta(days=STALE_DAYS)
    chosen = set(body.ids) if body.ids is not None else None
    wanted = []
    for r in rows:
        if body.scope == "product" and r["id"] != body.id:
            continue
        if body.scope == "caliber" and r["caliber_id"] != body.id:
            continue
        if body.scope != "product" and body.in_stock_only and not r["rounds"]:
            continue
        if body.scope == "stale" and updated.get(r["id"]) and updated[r["id"]] >= cutoff:
            continue
        if chosen is not None and r["id"] not in chosen:
            continue
        wanted.append({"id": r["id"], "label": r["label"], "caliber": r["caliber"], "brand": r["brand"], "name": r["name"],
                       "grain": r["bullet_weight_gr"], "bullet_type": r["bullet_type"], "rounds_per_box": r["rounds_per_box"],
                       "current": r["cost_per_round"], "slug": slugs.get(str(r["caliber_id"])),
                       "case_material": r["case_material"], "is_blank": r["is_blank"], "words": words.get(str(r["caliber_id"]), {}),
                       "price_updated": iso(updated[r["id"]]) if updated.get(r["id"]) else None})
    if body.scope in ("product", "caliber") and not wanted:
        raise HTTPException(404, "Nothing to price for that choice")
    return sorted(wanted, key=lambda p: (p["caliber"] or "", p["label"].lower()))


@router.post("/plan")
def plan(body: ScopeIn, db: Session = Depends(get_db)):
    products = _products(db, body)
    pending = sorted({p["caliber"] for p in products if not p["slug"] and not pricing.skip_reason(p)})
    per_minute = _per_minute(db)
    calls = pricing.planned_calls(products)
    skipped = [{"label": p["label"], "reason": pricing.skip_reason(p)} for p in products if pricing.skip_reason(p)]
    items = []
    for p in products:
        note = pricing.skip_reason(p) or (None if p["slug"] else "Caliber isn't matched to an AmmoSeek caliber yet")
        key = None if note else str(pricing.search_key(p["slug"], pricing.query_for(p, p.get("words"))))
        items.append({"id": p["id"], "label": p["label"], "caliber": p["caliber"], "current": p["current"],
                      "price_updated": p["price_updated"], "note": note, "key": key})
    return {"products": len(products), "calls": calls, "minutes": round(calls / per_minute, 1), "unmatched": pending, "skipped": skipped,
            "per_minute": per_minute, "items": items, "daily_remaining": pricing.daily_left(),
            "key_set": bool(_get(db, PRICE_KEY)), "options_loaded": bool(_json(db, OPTIONS_KEY, []))}


@router.post("/jobs")
def start_job(body: ScopeIn, db: Session = Depends(get_db)):
    key = _get(db, PRICE_KEY)
    if not key:
        raise HTTPException(400, "Enter the price service API key in Settings first")
    products = _products(db, body)
    if not products:
        raise HTTPException(400, "Choose at least one product")
    return pricing.start(products, key, _per_minute(db), _tax(db)).view()


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = pricing.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "That price check is no longer available")
    return job.view()


@router.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
def job_log(job_id: str):
    """What was asked of the price service, what came back and how each price was chosen (kept in memory only)."""
    job = pricing.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "That price check is no longer available")
    return PlainTextResponse(job.log_text(), headers={"Cache-Control": "no-store"})


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = pricing.JOBS.get(job_id)
    if job:
        job.cancel.set()
    return {"ok": True}


class ApplyItem(BaseModel):
    product_id: int
    price: float = Field(gt=0, le=10000)  # dollars per round


class ApplyIn(BaseModel):
    items: list[ApplyItem] = Field(min_length=1, max_length=2000)


@router.post("/apply")
def apply(body: ApplyIn, db: Session = Depends(get_db)):
    """Save accepted prices: each product's cost per box becomes the price per round times its rounds per box."""
    now, saved = utcnow(), 0
    for item in body.items:
        p = db.get(Product, item.product_id)
        if p:
            p.cost_per_box = round(item.price * p.rounds_per_box, 4)
            p.price_updated = now
            saved += 1
    db.commit()
    return {"saved": saved}
