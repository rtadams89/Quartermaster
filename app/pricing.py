"""Market price per round lookups through the parse.bot AmmoSeek API."""
import json
import logging
import re
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from difflib import SequenceMatcher

BASE_URL = "https://api.parse.bot/scraper/0a603c5c-dac6-4a0a-a758-05abdfc3060e/"
TIMEOUT = 40
PAGE_LENGTH = 40
MIN_SHIPPING = 50  # shipping score in tenths (50 is 5.0)
SHIPPING_BUCKET = "high"  # service filter: rating above 4
BAND = 2  # factor above and below the current price
CASINGS_SENT = {"brass", "steel", "aluminum", "composite", "alloy"}
RETRY_WAITS = (20, 40, 60)  # seconds to wait after each 429 before trying again
MAX_RETRY_AFTER = 120  # longest Retry-After that is waited out
SKIPPED_BRANDS = {"", "misc", "miscellaneous", "surplus", "unknown", "other", "handload", "handloads", "reload", "reloads"}
GENERIC_WORDS = {"loose", "box", "boxes", "ammo", "ammunition", "bulk", "the", "and", "for", "round", "rounds", "case", "cases"}
TYPE_WORDS = {
    "fmj": ("fmj", "full metal jacket"), "jhp": ("jhp", "hollow point", "hp"), "hp": ("hp", "hollow point", "jhp"),
    "sp": ("soft point", "sp", "jsp"), "jsp": ("jsp", "soft point"), "lrn": ("lrn", "lead round nose"),
    "rn": ("round nose", "rn"), "lfn": ("lfn", "flat nose"), "rnfp": ("rnfp", "flat point", "flat nose"),
    "tmj": ("tmj", "total metal jacket"), "bthp": ("bthp", "hollow point"), "ap": ("ap", "armor piercing"),
    "blank": ("blank", "blanks"), "blanks": ("blank", "blanks"),
}


class PriceError(Exception):
    """A search failed. `fatal`: no further search will work."""

    def __init__(self, message: str, fatal: bool = False, rate_limited: bool = False, stale: bool = False, retry_after: int | None = None, detail: str = ""):
        super().__init__(message)
        self.detail = detail
        self.retry_after = retry_after
        self.fatal = fatal
        self.rate_limited = rate_limited
        self.stale = stale


def _pause(seconds: float, cancel: threading.Event | None = None) -> None:
    """Sleep, returning early if `cancel` is set."""
    if cancel is None:
        time.sleep(seconds)
    else:
        cancel.wait(seconds)


def fetch(endpoint: str, params: dict, key: str):
    """One GET to the price service; returns the decoded JSON."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(f"{BASE_URL}{endpoint}" + (f"?{query}" if query else ""), headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return _checked(json.loads(resp.read().decode("utf-8", "replace")))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500] if hasattr(e, "read") else ""
        shown = {k: v for k, v in (e.headers.items() if getattr(e, "headers", None) else []) if k.lower() != "set-cookie"}
        detail = f"HTTP {e.code} {e.reason}; headers {json.dumps(shown)}; body {body!r}"
        if e.code in (401, 403):
            raise PriceError("The price service did not accept the API key.", fatal=True, detail=detail)
        if e.code == 429:
            after = str(e.headers.get("Retry-After", "") if getattr(e, "headers", None) else "").strip()
            raise PriceError("The price service's rate limit was reached.", rate_limited=True, retry_after=int(after) if after.isdigit() else None, detail=detail)
        raise PriceError(f"The price service answered with an error ({e.code}).", stale="stale_input" in body, detail=detail)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PriceError("Could not reach the price service from the server.", fatal=True)
    except ValueError:
        raise PriceError("The price service sent a reply that could not be read.")


def _checked(js):
    """The decoded reply, or a PriceError when the service says the request was not a success."""
    if isinstance(js, dict) and js.get("status") not in (None, "success"):
        text = json.dumps(js)[:300]
        raise PriceError(f"The price service reported a problem: {text}", stale="stale_input" in text)
    return js


def _payload(js):
    return js.get("data", js) if isinstance(js, dict) else js


def _find_dicts(node, field: str) -> list[dict]:
    """The first list of objects that carry `field`, wherever the reply nests it."""
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node) and any(field in x for x in node):
            return node
        for x in node:
            found = _find_dicts(x, field)
            if found:
                return found
    elif isinstance(node, dict):
        for x in node.values():
            found = _find_dicts(x, field)
            if found:
                return found
    return []


def caliber_options(key: str) -> list[dict]:
    """The calibers AmmoSeek offers: [{label, slug}]."""
    rows = _find_dicts(_payload(fetch("get_ammo_caliber_list", {}, key)), "seo")
    out = [{"label": str(r.get("label") or r["seo"]), "slug": str(r["seo"])} for r in rows if r.get("seo")]
    if not out:
        raise PriceError("The price service returned no calibers.")
    return sorted(out, key=lambda o: o["label"].lower())


def query_for(product: dict, words: dict | None = None) -> dict:
    """The search options for a product, beyond the caliber: grain, keywords, case material, blanks and the fixed filters."""
    grains, brand_word = search_words(product)
    words = words or {}
    casing = str(product.get("case_material") or "").strip().lower()
    extra = (words.get("include") or "").strip()
    include = "blank" if brandless(product) and not extra else " ".join(w for w in (brand_word, extra) if w)
    return {"grains": grains, "include": include,
            "exclude": (words.get("exclude") or "").strip(), "condition": "new", "hide_purchase_limits": "true",
            "shipping_rating": SHIPPING_BUCKET, "show_blanks": "true" if product.get("is_blank") else "false",
            "casing": casing if casing in CASINGS_SENT else ""}


def search_key(slug: str, query: dict) -> tuple:
    return (slug, tuple(sorted((k, v) for k, v in query.items() if v not in (None, ""))))


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def describe(it: dict) -> str:
    """One line for a listing: retailer, maker, description, price, count, per-round price, shipping, limit, case and condition."""
    each = per_round(it)
    return (f"{it.get('retailer', '?')} | {it.get('mfg', '')} | {str(it.get('descr', ''))[:90]} | {it.get('price', '?')} for {it.get('count', '?')} "
            f"= {'$%.4f/rd' % each if each else 'n/a'} | {it.get('grains', '?')}gr | ship {it.get('shipping_rating', '?')} | limit {it.get('purchaselimit', '?')} "
            f"| {it.get('casing', '?')} | {it.get('condition', '?')}")


def search(key: str, slug: str, query: dict, log: list[str] | None = None, cancel: threading.Event | None = None) -> list[dict]:
    """The first page (40 listings, cheapest first) of in-stock listings for a caliber, narrowed by the query."""
    log = [] if log is None else log
    params = {"caliber": slug, "length": PAGE_LENGTH, "start": 0, **{k: v for k, v in query.items() if v not in (None, "")}}
    retries = 0
    dropped = False
    while True:
        log.append(f"{_stamp()} REQUEST search_ammo {json.dumps(params)}")
        try:
            data = _payload(fetch("search_ammo", params, key))
            break
        except PriceError as e:
            if e.detail:
                log.append(f"  service replied: {e.detail}")
            if e.rate_limited and e.retry_after and e.retry_after > MAX_RETRY_AFTER:
                hours, minutes = divmod(e.retry_after // 60, 60)
                reset = f"{hours} h {minutes} min" if hours else f"{minutes} min"
                log.append(f"  FAILED: {e}")
                raise PriceError(f"The price service's request limit was reached and resets in about {reset}. Try again then.", rate_limited=True)
            if e.rate_limited and retries < len(RETRY_WAITS):
                wait = min(e.retry_after, MAX_RETRY_AFTER) if e.retry_after else RETRY_WAITS[retries]
                retries += 1
                log.append(f"  rate limited; waiting {wait} seconds, then trying again ({retries} of {len(RETRY_WAITS)})")
                _pause(wait, cancel)
                if cancel is not None and cancel.is_set():
                    raise PriceError("Cancelled.", fatal=True)
            elif e.rate_limited:
                log.append(f"  FAILED: {e}")
                raise PriceError("The price service's rate limit did not clear after waiting; try again later (a monthly credit limit looks the same).", rate_limited=True)
            elif e.stale and "casing" in params and not dropped:
                dropped = True
                params.pop("casing")
                log.append("  the service rejected an option; trying again without the case-material filter (listings are still checked for it here)")
            else:
                log.append(f"  FAILED: {e}")
                raise
    items = data.get("items") if isinstance(data, dict) else None
    if items is None:
        items = _find_dicts(data, "retailer")
    items = [i for i in items if isinstance(i, dict)]
    meta = f" (service total {data.get('total')}, filtered {data.get('filtered')})" if isinstance(data, dict) and "total" in data else ""
    log.append(f"  RESPONSE {len(items)} listings{meta}")
    log.extend(f"    {n}. {describe(it)}" for n, it in enumerate(items, 1))
    return items


# ------------------------------------------------------------------ matching
def squash(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _words(text: str, minimum: int = 2) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) >= minimum]


def parse_money(text) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"\d[\d,]*\.?\d*", str(text or ""))
    return float(m.group(0).replace(",", "")) if m else None


def per_round(item: dict) -> float | None:
    """Dollars per round for a listing: its price divided by its round count, else its cents-per-round figure."""
    price, count = parse_money(item.get("price")), item.get("count")
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    if price and count > 0:
        return price / count
    cents = parse_money(item.get("cp_cleaned"))
    return cents / 100 if cents else None


def has_purchase_limit(item: dict) -> bool:
    return bool(re.search(r"[1-9]", str(item.get("purchaselimit") or "")))


def good_shipping(item: dict) -> bool:
    """Free shipping, or a shipping score of 5.0 or more ("cf 76" counts as 7.6)."""
    rating = str(item.get("shipping_rating") or "").strip().lower()
    if rating == "free":
        return True
    m = re.fullmatch(r"(?:cf\s*)?(\d+(?:\.\d+)?)", rating)
    return bool(m) and float(m.group(1)) >= MIN_SHIPPING


def eligible(item: dict) -> bool:
    return good_shipping(item) and not has_purchase_limit(item)


def search_words(product: dict) -> tuple[int | None, str]:
    """(grain, brand word) used to narrow the search for a product."""
    grain = product.get("grain")
    grains = int(round(grain)) if grain and grain > 0 else None
    words = sorted(re.findall(r"[A-Za-z0-9]+", product.get("brand", "")), key=len, reverse=True)
    return grains, (words[0] if words else "")


def brandless(product: dict) -> bool:
    return squash(product.get("brand")) in {squash(b) for b in SKIPPED_BRANDS}


def skip_reason(product: dict) -> str | None:
    """The reason a product is not priced: mixed, surplus or unbranded ammo other than blanks."""
    if brandless(product) and not product.get("is_blank"):
        return "Mixed, surplus or unbranded ammo can't be priced automatically"
    return None


def _type_score(bullet_type: str, descr: str) -> float:
    words = [w for w in _words(bullet_type) if w not in GENERIC_WORDS]
    if not words:
        return 1.0
    hit = 0
    for w in words:
        options = TYPE_WORDS.get(w, (w,))
        if any(re.search(rf"\b{re.escape(o)}\b", descr) for o in options):
            hit += 1
    return hit / len(words)


def _name_score(name: str, descr: str) -> float:
    words = [w for w in _words(name, 3) if w not in GENERIC_WORDS]
    if not words:
        return 1.0
    return sum(1 for w in words if w in descr) / len(words)


def _has_words(words: str, text: str) -> list[str]:
    """The words (split on spaces) that do not appear in the text."""
    return [w for w in words.lower().split() if w not in text]


def reject_reason(product: dict, it: dict, words: dict) -> str | None:
    """Why a listing cannot be used for the product, or None when it can."""
    descr = str(it.get("descr") or "").lower()
    maker = squash(it.get("mfg"))
    brand, grain = squash(product.get("brand")), product.get("grain")
    if str(it.get("condition") or "new").lower() != "new":
        return f"condition is {it.get('condition')}, not new"
    if not good_shipping(it):
        return f"shipping rating {it.get('shipping_rating')!r} is below 50 and not free"
    if has_purchase_limit(it):
        return f"has a purchase limit ({it.get('purchaselimit')})"
    is_blank = bool(re.search(r"\bblanks?\b", descr))
    if product.get("is_blank") and not is_blank:
        return "product is a blank but the listing does not say blank"
    if is_blank and not product.get("is_blank"):
        return "listing is a blank but the product is not"
    case, listed = str(product.get("case_material") or "").strip().lower(), str(it.get("casing") or "").strip().lower()
    if case and listed and case not in listed and listed not in case:
        return f"case is {listed}, not {case}"
    if not brandless(product) and not (brand and ((maker and (brand in maker or maker in brand)) or brand in squash(descr))):
        return "brand does not match"
    text = f"{descr} {str(it.get('mfg') or '').lower()}"
    missing = _has_words(words.get("include") or "", text)
    if missing:
        return f"must-include word missing: {' '.join(missing)}"
    present = [w for w in (words.get("exclude") or "").lower().split() if w in text]
    if present:
        return f"must-exclude word present: {' '.join(present)}"
    if grain and grain > 0:
        try:
            if abs(int(it.get("grains")) - round(grain)) > 1:
                return f"grain {it.get('grains')} is not within 1 of {round(grain)}"
        except (TypeError, ValueError):
            return "listing has no grain weight"
    each = per_round(it)
    if not each:
        return "no price per round"
    try:
        count = int(it.get("count"))
    except (TypeError, ValueError):
        count = 0
    if (product.get("rounds_per_box") or 0) > 1 and count <= 1:
        return f"listing counts {count} round but the product is sold by the box"
    current = product.get("current")
    if current and not current / BAND <= each <= current * BAND:
        return f"price ${each:.4f}/rd is less than half or more than double the current ${current:.4f}/rd"
    return None


def estimate(product: dict, items: list[dict], words: dict | None = None, trace: list[str] | None = None) -> dict:
    """Average price per round of the matching listings with good shipping and no purchase limit, with how sure the match is."""
    words = words or {}
    trace = [] if trace is None else trace
    tiers: list[list[float]] = [[], [], []]
    out_of_band = 0
    for it in items:
        why = reject_reason(product, it, words)
        if why:
            out_of_band += why.startswith("price ")
            trace.append(f"    skip: {why} | {describe(it)}")
            continue
        descr = str(it.get("descr") or "").lower()
        price = per_round(it)
        name_ok, type_ok = _name_score(product.get("name", ""), descr), _type_score(product.get("bullet_type", ""), descr)
        if name_ok == 1.0 and type_ok >= 0.5:
            tiers[0].append(price)
        if name_ok >= 0.5:
            tiers[1].append(price)
        tiers[2].append(price)
        trace.append(f"    usable: name match {name_ok:.0%}, bullet type match {type_ok:.0%}, ${price:.4f}/rd | {describe(it)}")
    names = ("brand, grain, full name and bullet type", "brand, grain and part of the name", "brand and grain only")
    trace.append("    usable listings by match level: " + "; ".join(f"{names[i]}: {len(t)}" for i, t in enumerate(tiers)))
    tier = next((i for i, t in enumerate(tiers) if len(t) >= 3), next((i for i, t in enumerate(tiers) if t), None))
    if tier is None:
        note = ("No in-stock listings found" if not items else "Listings found, but all were under half or over double the current price" if out_of_band
                else "No matching listings with shipping 5.0+ and no purchase limit")
        return {"price": None, "listings": 0, "confidence": "none", "note": note}
    prices = sorted(tiers[tier])
    if not product.get("current") and len(prices) >= 5:
        middle = statistics.median(prices)
        kept = [x for x in prices if middle / BAND <= x <= middle * BAND] or prices
        if len(kept) != len(prices):
            trace.append(f"    no current price, so {len(prices) - len(kept)} price(s) under half or over double the median ${middle:.4f}/rd were dropped")
        prices = kept
    mid = statistics.mean(prices)
    n = len(prices)
    trace.append(f"    chosen: the first match level with 3 or more listings, else the first with any ({names[tier]}); "
                 f"average of {n} price{'s' if n != 1 else ''} = ${mid:.4f}/rd")
    if tier == 0 and n >= 3:
        confidence = "high"
    elif (tier == 0) or (tier == 1 and n >= 3):
        confidence = "medium"
    else:
        confidence = "low"
    notes = {0: "", 1: "Matched on brand, grain and part of the name", 2: "Matched on brand and grain only"}
    note = notes[tier] + ("; " if notes[tier] and n < 3 else "") + (f"only {n} listing{'s' if n != 1 else ''}" if n < 3 else "")
    if brandless(product):
        confidence = "low" if confidence == "low" else "medium"
        note = (note + "; " if note else "") + "no brand to match, so priced from all blank listings for the caliber"
    return {"price": round(mid, 4), "low": round(prices[0], 4), "high": round(prices[-1], 4), "listings": n, "confidence": confidence, "note": note}


def guess_slug(names: list[str], options: list[dict]) -> str | None:
    """The AmmoSeek caliber that looks most like one of the given names, or None."""
    clean = [squash(re.sub(r"\b(subsonic|supersonic|blanks?)\b", "", n, flags=re.I)) for n in names if n]
    best, best_score = None, 0.0
    for o in options:
        for c in clean:
            if not c:
                continue
            label = squash(o["label"])
            score = 1.0 if c == label else SequenceMatcher(None, c, label).ratio()
            if score > best_score:
                best, best_score = o["slug"], score
    return best if best_score >= 0.8 else None


# ------------------------------------------------------------------ jobs
class Job:
    def __init__(self, products: list[dict], key: str, per_minute: int):
        self.id = uuid.uuid4().hex[:12]
        self.products = products
        self.key = key
        self.gap = 60.0 / max(1, per_minute)
        self.state = "running"  # running | done | cancelled | stopped
        self.message = ""
        self.calls = 0
        self.results: list[dict] = []
        self.cancel = threading.Event()
        self.log: list[str] = [f"Quartermaster price check {self.id} started {datetime.now():%Y-%m-%d %H:%M:%S}",
                               f"{len(products)} product(s); at most {per_minute} search(es) a minute.",
                               "Searches ask for the first 40 listings (cheapest first), new condition, purchase limits hidden and shipping rating above 4;",
                               "listings are then checked again here (shipping 5.0+ or free, no limit, not a one-round count, within half to double the current price) and averaged per round.", ""]

    def view(self) -> dict:
        return {"id": self.id, "state": self.state, "message": self.message, "total": len(self.products),
                "done": len(self.results), "calls": self.calls, "results": self.results}

    def log_text(self) -> str:
        return "\n".join(self.log) + "\n"


JOBS: dict[str, Job] = {}
_lock = threading.Lock()


def start(products: list[dict], key: str, per_minute: int) -> Job:
    """Begin a job in the background. Each product is {id, label, brand, name, grain, bullet_type, case_material, is_blank, words, slug, current}."""
    job = Job(products, key, per_minute)
    with _lock:
        for old in [j for j in JOBS.values() if j.state != "running"][:-4]:
            JOBS.pop(old.id, None)
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def planned_calls(products: list[dict]) -> int:
    return len({search_key(p["slug"], query_for(p, p.get("words"))) for p in products if p.get("slug") and not skip_reason(p)})


def _describe_product(p: dict) -> list[str]:
    cur = p.get("current")
    words = p.get("words") or {}
    return [f"=== {p['label']} ({p.get('caliber', '')}) ===",
            f"  product: brand {p.get('brand') or '-'}, name {p.get('name') or '-'}, grain {p.get('grain') or '-'}, bullet type {p.get('bullet_type') or '-'}, "
            f"case {p.get('case_material') or '-'}, blank {'yes' if p.get('is_blank') else 'no'}, current price {'$%.4f/rd' % cur if cur else 'none'}",
            f"  caliber match: {p.get('slug') or 'none'}; must include '{words.get('include', '')}', must exclude '{words.get('exclude', '')}'"]


def _run(job: Job) -> None:
    cache: dict[tuple, list[dict]] = {}
    last = 0.0
    try:
        for p in job.products:
            if job.cancel.is_set():
                job.state = "cancelled"
                job.log.append("Cancelled.")
                return
            row = {"id": p["id"], "label": p["label"], "caliber": p.get("caliber", ""), "current": p.get("current"), "rounds_per_box": p.get("rounds_per_box")}
            job.log.extend(_describe_product(p))
            reason = skip_reason(p) or (None if p.get("slug") else "This caliber isn't matched to an AmmoSeek caliber yet")
            if reason:
                job.results.append({**row, "price": None, "listings": 0, "confidence": "none", "note": reason})
                job.log.extend([f"  skipped: {reason}", ""])
                continue
            words = p.get("words") or {}
            query = query_for(p, words)
            ck = search_key(p["slug"], query)
            if ck not in cache:
                wait = job.gap - (time.monotonic() - last)
                if last and wait > 0:
                    _pause(wait, job.cancel)
                first = len(job.log)
                try:
                    cache[ck] = search(job.key, p["slug"], query, job.log, job.cancel)
                except PriceError as e:
                    last = time.monotonic()
                    job.calls += sum("REQUEST" in x for x in job.log[first:])
                    if job.cancel.is_set():
                        job.state = "cancelled"
                        job.log.append("Cancelled.")
                        return
                    job.results.append({**row, "price": None, "listings": 0, "confidence": "none", "note": str(e)})
                    if e.fatal or e.rate_limited:
                        job.state, job.message = "stopped", str(e)
                        job.log.append(f"  stopped: {e}")
                        return
                    job.log.extend([f"  no result: {e}", ""])
                    continue
                last = time.monotonic()
                job.calls += sum("REQUEST" in x for x in job.log[first:])
            else:
                job.log.append("  search: same options as an earlier product, so that search's listings are reused (no new request)")
            trace: list[str] = []
            result = estimate(p, cache[ck], words, trace)
            job.log.extend(trace)
            job.results.append({**row, **result})
            shown = f"${result['price']:.4f}/rd from {result['listings']} listing(s)" if result["price"] else "no price"
            job.log.extend([f"  RESULT: {shown}; confidence {result['confidence']}; {result['note'] or 'good match'}", ""])
        job.state = "done"
        job.log.append("Done.")
    except Exception:
        logging.getLogger(__name__).exception("price job %s failed", job.id)
        job.state, job.message = "stopped", "Stopped unexpectedly; see the server log."
        job.log.append(job.message)
