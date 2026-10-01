from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .assets import FreshStatic
from .db import Base, SessionLocal, engine
from .routers import auth, backup, batches, catalog, inventory, labels, photos, settings
from .seed import seed

STATIC = Path(__file__).parent / "static"

Base.metadata.create_all(engine)
with SessionLocal() as _db:
    seed(_db)

app = FastAPI(title="Quartermaster", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)


class NoStore(BaseHTTPMiddleware):
    """The UIs are tiny; never let a kiosk keep a stale copy after an update."""

    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        if not request.url.path.startswith("/api/") or request.url.path.startswith("/api/labels/render"):
            if "cache-control" not in resp.headers:
                resp.headers["Cache-Control"] = "no-cache"
        else:
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp


app.add_middleware(NoStore)

for r in (auth.router, batches.router, catalog.router, photos.router, inventory.router, labels.router,
          settings.router, backup.router):
    app.include_router(r)

app.mount("/shared", FreshStatic(directory=STATIC / "shared"), name="shared")
app.mount("/kiosk", FreshStatic(directory=STATIC / "kiosk", html=True), name="kiosk")
app.mount("/admin", FreshStatic(directory=STATIC / "admin", html=True), name="admin")


# Browsers ask for icons at the site root regardless of which page they are on.
for _name, _type in (("favicon.svg", "image/svg+xml"), ("favicon.ico", "image/x-icon"), ("apple-touch-icon.png", "image/png")):
    def _icon(_name=_name, _type=_type):
        return FileResponse(STATIC / "shared" / _name, media_type=_type)
    app.add_api_route("/" + _name, _icon, methods=["GET"], include_in_schema=False)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Quartermaster</title><link rel=icon href=/favicon.svg type=image/svg+xml>
<body style="font:16px system-ui;background:#10151c;color:#e8edf3;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h1>Quartermaster</h1>
<p><a style="color:#6cb6ff" href="/kiosk/">Kiosk</a> &nbsp;·&nbsp; <a style="color:#6cb6ff" href="/admin/">Admin</a></p></div>"""
