"""Online UPC lookup (UPCitemdb free "trial" endpoint) plus a guess at the product details.

Only the barcode number leaves the server. The result is a suggestion for the admin to
confirm; nothing is saved from it automatically.
"""
import io
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from PIL import Image, ImageStat
from sqlalchemy.orm import Session

from .db import utcnow
from .models import Caliber, UpcLookup

URL = "https://api.upcitemdb.com/prod/trial/lookup?upc="
TIMEOUT = 8
NOT_FOUND_RETRY = timedelta(days=7)  # a miss is asked again after this long; hits are kept


class LookupFailed(Exception):
    """The service could not be asked (offline, rate limited, bad reply). Never cached."""


def is_lookupable(code: str) -> bool:
    """Only real retail barcodes (UPC-E/EAN-8, UPC-A, EAN-13) are worth asking about."""
    return code.isdigit() and len(code) in (8, 12, 13)


def _fetch(code: str) -> dict:
    req = urllib.request.Request(URL + code, headers={"User-Agent": "Quartermaster", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise LookupFailed("The daily lookup limit was reached. Try again tomorrow, or fill the details in by hand.")
        if e.code == 404:
            return {"items": []}
        raise LookupFailed(f"The lookup service answered with an error ({e.code}).")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise LookupFailed("Could not reach the lookup service from the server.")


def lookup(db: Session, code: str) -> UpcLookup:
    row = db.get(UpcLookup, code)
    if row and (row.found or utcnow() - row.fetched_at < NOT_FOUND_RETRY):
        return row
    data = _fetch(code)
    items = data.get("items") if isinstance(data, dict) else None
    if data.get("code") not in (None, "OK") and not items:
        if data.get("code") == "EXCEED_LIMIT":
            raise LookupFailed("The daily lookup limit was reached. Try again tomorrow, or fill the details in by hand.")
        items = []
    item = items[0] if items else None
    if row is None:
        row = UpcLookup(code=code)
        db.add(row)
    row.found = bool(item)
    row.title = str((item or {}).get("title") or "")[:300]
    row.brand = str((item or {}).get("brand") or "")[:120]
    row.description = str((item or {}).get("description") or "")[:2000]
    images = (item or {}).get("images") or []
    row.image_url = next((i for i in images if isinstance(i, str) and i.startswith("https://")), "")[:500]
    row.fetched_at = utcnow()
    db.commit()
    return row


# ---------------------------------------------------------------- guessing details
# Names that mean the same caliber (spaces, dots and case are ignored when comparing).
_GROUPS = [
    {"9mm", "9mmluger", "9mmpara", "9x19", "9x19mm"},
    {"45acp", "45auto"},
    {"223", "223rem", "223remington", "556", "556x45", "556nato", "556x45mm"},
    {"308", "308win", "308winchester", "762x51", "762x51mm"},
    {"40sw", "40s&w", "40smithwesson"},
    {"380", "380acp", "380auto"},
    {"22lr", "22longrifle"},
    {"12ga", "12gauge", "12guage"},
    {"20ga", "20gauge"},
    {"38special", "38spl"},
    {"357mag", "357magnum"},
    {"3006", "3006springfield", "3006sprg"},
    {"762x39", "762x39mm"},
    {"10mm", "10mmauto"},
    {"300aacblackout", "300blackout", "300blk", "300aac"},
    {"65creedmoor", "65cm"},
    {"44magnum", "44mag"},
    {"22wmr", "22magnum", "22winmag"},
]
_BULLETS = ["FMJ", "TMJ", "JHP", "HP", "SP", "LRN", "LSWC", "BTHP", "SMK"]


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9&]", "", text.lower())


def _terms(name: str) -> set[str]:
    """What a listing might call this caliber. '.223 Rem / 5.56 NATO' counts as both halves."""
    out = {_squash(name)}
    for part in re.split(r"\s*(?:/|,|\bor\b)\s*", name):
        key = _squash(part)
        out.add(key)
        for g in _GROUPS:
            if key in g:
                out |= g
    out.discard("")
    return out


def _title_terms(text: str) -> set[str]:
    """Every run of up to three consecutive words, squashed: '45 ACP' -> '45acp'."""
    words = re.findall(r"[A-Za-z0-9.&]+", text)
    return {_squash("".join(words[i:i + n])) for i in range(len(words)) for n in (1, 2, 3)}


def guess(db: Session, row: UpcLookup) -> dict:
    """Best-effort product details from a listing's title. Anything unsure is left out."""
    title = row.title
    seen = _title_terms(title)
    out: dict = {}
    best = 0
    for c in db.query(Caliber).filter(Caliber.active.is_(True)).all():
        hit = max((len(t) for t in _terms(c.name) if t in seen), default=0)
        if hit > best:
            out["caliber_id"], best = c.id, hit
    m = (re.search(r"(\d{1,4})\s*(?:rounds?|rds?|ct|count|pk|pack)\b", title, re.I)
         or re.search(r"box of\s*(\d{1,4})", title, re.I)
         or re.search(r"(\d{1,4})\s*per box", title, re.I))
    if m and 1 <= int(m.group(1)) <= 1000:
        out["rounds_per_box"] = int(m.group(1))
    m = re.search(r"(\d{2,3}(?:\.\d)?)\s*(?:gr\b|grain)", title, re.I)
    if m:
        out["bullet_weight_gr"] = float(m.group(1))
    for b in _BULLETS:
        if re.search(rf"\b{b}\b", title, re.I):
            out["bullet_type"] = b
            break
    brand = row.brand.strip()
    out["brand"] = brand
    name = title
    if brand and name.lower().startswith(brand.lower()):
        name = name[len(brand):].lstrip(" -:,")
    out["name"] = name.strip()[:120]
    return out


# ---------------------------------------------------------------- listing photo
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MIN_SIDE = 200          # smaller than this is a thumbnail or an icon, not a product photo
MIN_DETAIL = 12         # grayscale spread below this is a blank or flat-colour placeholder


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: ANN002, ANN003
        return None  # we follow redirects ourselves so every hop is checked


def _public_host(host: str) -> bool:
    """True only if every address the name resolves to is a public internet address."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def fetch_image(url: str) -> bytes:
    """Download a listing photo. https only, public hosts only, small, and really an image."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(4):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or not _public_host(parts.hostname):
            raise LookupFailed("The listing photo is not at a usable address.")
        req = urllib.request.Request(url, headers={"User-Agent": "Quartermaster", "Accept": "image/*"})
        try:
            with opener.open(req, timeout=TIMEOUT) as r:
                if not (r.headers.get("Content-Type") or "").lower().startswith("image/"):
                    raise LookupFailed("The listing photo is not an image.")
                data = r.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if not loc:
                raise LookupFailed(f"The listing photo could not be downloaded ({e.code}).")
            url = urllib.parse.urljoin(url, loc)
            continue
        except (urllib.error.URLError, TimeoutError, OSError):
            raise LookupFailed("The listing photo could not be downloaded.")
        if len(data) > MAX_IMAGE_BYTES:
            raise LookupFailed("The listing photo is too large.")
        return data
    raise LookupFailed("The listing photo redirected too many times.")


def looks_like_product_photo(data: bytes) -> bool:
    """Reject tiny images, extreme shapes and flat/blank placeholders; keep real photos."""
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        if min(w, h) < MIN_SIDE or not 0.4 <= w / h <= 2.5:
            return False
        small = img.convert("L")
        small.thumbnail((128, 128))
        return ImageStat.Stat(small).stddev[0] >= MIN_DETAIL
    except (OSError, ValueError, Image.DecompressionBombError):
        return False
