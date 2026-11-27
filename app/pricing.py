"""Current market price per round, looked up on request through the parse.bot AmmoSeek API.

A price job makes one search per (caliber, grain, brand) and reduces the listings that match each product
to an average price per round. The admin reviews the result before any price is saved.
"""
import json
import re
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from difflib import SequenceMatcher

BASE_URL = "https://api.parse.bot/scraper/0a603c5c-dac6-4a0a-a758-05abdfc3060e/"
TIMEOUT = 40
PAGE_LENGTH = 40
MIN_SHIPPING = 50  # AmmoSeek shipping score in tenths; higher is cheaper shipping
RETRY_WAIT = 20  # seconds to wait after a 429 before trying once more
SKIPPED_BRANDS = {"", "misc", "miscellaneous", "surplus", "unknown", "other", "handload", "handloads", "reload", "reloads"}
GENERIC_WORDS = {"loose", "box", "boxes", "ammo", "ammunition", "bulk", "the", "and", "for", "round", "rounds", "case", "cases"}
TYPE_WORDS = {
    "fmj": ("fmj", "full metal jacket"), "jhp": ("jhp", "hollow point", "hp"), "hp": ("hp", "hollow point", "jhp"),
    "sp": ("soft point", "sp", "jsp"), "jsp": ("jsp", "soft point"), "lrn": ("lrn", "lead round nose"),
    "rn": ("round nose", "rn"), "lfn": ("lfn", "flat nose"), "rnfp": ("rnfp", "flat point", "flat nose"),
    "tmj": ("tmj", "total metal jacket"), "bthp": ("bthp", "hollow point"), "ap": ("ap", "armor piercing"),
}


class PriceError(Exception):
    """A search could not be made. `fatal` means no further search will work (bad key, no connection)."""

    def __init__(self, message: str, fatal: bool = False, rate_limited: bool = False):
        super().__init__(message)
        self.fatal = fatal
        self.rate_limited = rate_limited


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def fetch(endpoint: str, params: dict, key: str):
    """One GET to the price service; returns the decoded JSON."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(f"{BASE_URL}{endpoint}" + (f"?{query}" if query else ""), headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PriceError("The price service did not accept the API key.", fatal=True)
        if e.code == 429:
            raise PriceError("The price service's rate limit was reached.", rate_limited=True)
        raise PriceError(f"The price service answered with an error ({e.code}).")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PriceError("Could not reach the price service from the server.", fatal=True)
    except ValueError:
        raise PriceError("The price service sent a reply that could not be read.")


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


def search(key: str, slug: str, grains: int | None, include: str) -> list[dict]:
    """One page of in-stock listings for a caliber, optionally narrowed by grain and a brand word."""
    params = {"caliber": slug, "length": PAGE_LENGTH, "start": 0}
    if grains:
        params["grains"] = grains
    if include:
        params["include"] = include
    for attempt in (0, 1):
        try:
            data = _payload(fetch("search_ammo", params, key))
            break
        except PriceError as e:
            if not e.rate_limited or attempt:
                raise
            _pause(RETRY_WAIT)
    items = data.get("items") if isinstance(data, dict) else None
    if items is None:
        items = _find_dicts(data, "retailer")
    return [i for i in items if isinstance(i, dict)]


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
    """Free shipping, or a shipping score of 5.0 or better (the service sends tenths: 50 is 5.0)."""
    rating = str(item.get("shipping_rating") or "").strip().lower()
    if rating == "free":
        return True
    m = re.fullmatch(r"\d+(\.\d+)?", rating)
    return bool(m) and float(rating) >= MIN_SHIPPING


def eligible(item: dict) -> bool:
    return good_shipping(item) and not has_purchase_limit(item)


def search_words(product: dict) -> tuple[int | None, str]:
    """(grain, brand word) used to narrow the search for a product."""
    grain = product.get("grain")
    grains = int(round(grain)) if grain and grain > 0 else None
    words = sorted(re.findall(r"[A-Za-z0-9]+", product.get("brand", "")), key=len, reverse=True)
    return grains, (words[0] if words else "")


def skip_reason(product: dict) -> str | None:
    if squash(product.get("brand")) in {squash(b) for b in SKIPPED_BRANDS}:
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


def estimate(product: dict, items: list[dict]) -> dict:
    """Average price per round of the matching listings with good shipping and no purchase limit, with how sure the match is."""
    brand = squash(product.get("brand"))
    grain = product.get("grain")
    tiers: list[list[float]] = [[], [], []]
    for it in items:
        if not eligible(it):
            continue
        if str(it.get("condition") or "new").lower() != "new":
            continue
        descr = str(it.get("descr") or "").lower()
        maker = squash(it.get("mfg"))
        if not (brand and ((maker and (brand in maker or maker in brand)) or brand in squash(descr))):
            continue
        if grain and grain > 0:
            try:
                if abs(int(it.get("grains")) - round(grain)) > 1:
                    continue
            except (TypeError, ValueError):
                continue
        price = per_round(it)
        if not price:
            continue
        name_ok, type_ok = _name_score(product.get("name", ""), descr), _type_score(product.get("bullet_type", ""), descr)
        if name_ok == 1.0 and type_ok >= 0.5:
            tiers[0].append(price)
        if name_ok >= 0.5:
            tiers[1].append(price)
        tiers[2].append(price)
    tier = next((i for i, t in enumerate(tiers) if len(t) >= 3), next((i for i, t in enumerate(tiers) if t), None))
    if tier is None:
        note = "No matching listings with shipping 5.0+ and no purchase limit" if items else "No in-stock listings found"
        return {"price": None, "listings": 0, "confidence": "none", "note": note}
    prices = sorted(tiers[tier])
    mid = statistics.mean(prices)
    n = len(prices)
    if tier == 0 and n >= 3:
        confidence = "high"
    elif (tier == 0) or (tier == 1 and n >= 3):
        confidence = "medium"
    else:
        confidence = "low"
    notes = {0: "", 1: "Matched on brand, grain and part of the name", 2: "Matched on brand and grain only"}
    note = notes[tier] + ("; " if notes[tier] and n < 3 else "") + (f"only {n} listing{'s' if n != 1 else ''}" if n < 3 else "")
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

    def view(self) -> dict:
        return {"id": self.id, "state": self.state, "message": self.message, "total": len(self.products),
                "done": len(self.results), "calls": self.calls, "results": self.results}


JOBS: dict[str, Job] = {}
_lock = threading.Lock()


def start(products: list[dict], key: str, per_minute: int) -> Job:
    """Begin a job in the background. Each product is {id, label, brand, name, grain, bullet_type, slug, current}."""
    job = Job(products, key, per_minute)
    with _lock:
        for old in [j for j in JOBS.values() if j.state != "running"][:-4]:
            JOBS.pop(old.id, None)
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def planned_calls(products: list[dict]) -> int:
    return len({(p["slug"], *search_words(p)) for p in products if p.get("slug") and not skip_reason(p)})


def _run(job: Job) -> None:
    cache: dict[tuple, list[dict]] = {}
    last = 0.0
    try:
        for p in job.products:
            if job.cancel.is_set():
                job.state = "cancelled"
                return
            row = {"id": p["id"], "label": p["label"], "caliber": p.get("caliber", ""), "current": p.get("current"), "rounds_per_box": p.get("rounds_per_box")}
            reason = skip_reason(p) or (None if p.get("slug") else "This caliber isn't matched to an AmmoSeek caliber yet")
            if reason:
                job.results.append({**row, "price": None, "listings": 0, "confidence": "none", "note": reason})
                continue
            grains, include = search_words(p)
            ck = (p["slug"], grains, include)
            if ck not in cache:
                wait = job.gap - (time.monotonic() - last)
                if last and wait > 0:
                    _pause(wait)
                last = time.monotonic()
                job.calls += 1
                try:
                    cache[ck] = search(job.key, p["slug"], grains, include)
                except PriceError as e:
                    if e.fatal or e.rate_limited:
                        job.state, job.message = "stopped", str(e)
                        job.results.append({**row, "price": None, "listings": 0, "confidence": "none", "note": str(e)})
                        return
                    job.results.append({**row, "price": None, "listings": 0, "confidence": "none", "note": str(e)})
                    continue
            job.results.append({**row, **estimate(p, cache[ck])})
        job.state = "done"
    except Exception as e:
        job.state, job.message = "stopped", f"Stopped unexpectedly: {e}"
