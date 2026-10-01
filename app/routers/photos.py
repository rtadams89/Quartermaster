"""Box photos: one picture per barcode, taken at the kiosk or uploaded from the admin site."""
import hashlib
import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from .. import security
from ..codes import normalize_code
from ..db import get_db, utcnow
from ..models import Barcode, BarcodePhoto

router = APIRouter(prefix="/api/barcodes", dependencies=[Depends(security.require_auth)])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
FULL_SIZE = 1280
THUMB_SIZE = 240
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs


def process_image(data: bytes) -> tuple[bytes, bytes]:
    """Decode any common image, fix its rotation, drop metadata, return (full, thumb) JPEGs."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED_FORMATS:
            raise ValueError("unsupported format")
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.getchannel("A"))
            img = flat
        else:
            img = img.convert("RGB")
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "That file isn't a usable image (try a JPEG or PNG)")

    def encode(im: Image.Image, longest: int, quality: int) -> bytes:
        im = im.copy()
        im.thumbnail((longest, longest), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True)  # re-encoding strips EXIF/GPS
        return out.getvalue()

    return encode(img, FULL_SIZE, 85), encode(img, THUMB_SIZE, 78)


def _code(raw: str) -> str:
    try:
        return normalize_code(raw)
    except ValueError:
        raise HTTPException(400, "Invalid code")


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image is too large (15 MB max)")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Image is too large (15 MB max)")
        chunks.append(chunk)
    if not size:
        raise HTTPException(400, "No image received")
    return b"".join(chunks)


@router.get("/{code}/photo")
def get_photo(code: str, request: Request, thumb: bool = False, db: Session = Depends(get_db)):
    p = db.get(BarcodePhoto, _code(code))
    if not p:
        raise HTTPException(404, "No photo")
    etag = f'"{p.etag}{"-t" if thumb else ""}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}  # always revalidate, cheap via ETag
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(p.thumb if thumb else p.image, media_type="image/jpeg", headers=headers)


@router.put("/{code}/photo")
async def put_photo(code: str, request: Request, db: Session = Depends(get_db)):
    """Body is the raw image file. Creates the photo, or replaces the existing one."""
    code = _code(code)
    if not db.get(Barcode, code):
        raise HTTPException(404, "Code not found")
    full, thumb = process_image(await _read_body(request))
    p = db.get(BarcodePhoto, code)
    if p is None:
        p = BarcodePhoto(code=code)
        db.add(p)
    p.image, p.thumb = full, thumb
    p.etag = hashlib.sha1(full).hexdigest()
    p.updated_at = utcnow()
    db.commit()
    return {"ok": True, "bytes": len(full)}


@router.delete("/{code}/photo")
def delete_photo(code: str, db: Session = Depends(get_db)):
    p = db.get(BarcodePhoto, _code(code))
    if not p:
        raise HTTPException(404, "No photo")
    db.delete(p)
    db.commit()
    return {"ok": True}
