"""Serving the two web UIs so that an update is always picked up, without a forced refresh.

Two things used to go wrong:

* Files were revalidated with an ETag built from modification time + size. Copying files around (Docker
  layers, synced folders) can leave two different versions with the same pair, so a browser was told
  "unchanged" and kept the old one. The ETag here is a hash of the file's *content*.
* A page that was already open never noticed a new version at all. Every script is served with a
  ``BUILD`` constant (a hash of all UI files + the version) filled in, and ``/api/health`` reports the
  server's current build; the pages compare the two and reload themselves when they differ.
"""
import hashlib
import mimetypes
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from . import __version__

STATIC = Path(__file__).parent / "static"
PLACEHOLDER = b"__BUILD__"
TEXT_TYPES = {".js", ".html", ".css", ".svg"}


def compute_build() -> str:
    h = hashlib.sha256(__version__.encode())
    for p in sorted(STATIC.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(STATIC)).replace("\\", "/").encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


BUILD = compute_build()


class FreshStatic(StaticFiles):
    """StaticFiles with content-hash ETags, no heuristic caching, and the build id substituted into scripts."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        path = Path(full_path)
        data = path.read_bytes()
        if path.suffix in (".js", ".html"):
            data = data.replace(PLACEHOLDER, BUILD.encode())
        etag = '"' + hashlib.sha256(data).hexdigest()[:24] + '"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        sent = Headers(scope=scope).get("if-none-match")
        if status_code == 200 and sent and etag in [t.strip().removeprefix("W/") for t in sent.split(",")]:
            return Response(status_code=304, headers=headers)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return Response(data, status_code=status_code, media_type=media_type, headers=headers)
