from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .db import Base, SessionLocal, engine
from .routers import auth, batches, catalog, inventory, labels
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

for r in (auth.router, batches.router, catalog.router, inventory.router, labels.router):
    app.include_router(r)

app.mount("/shared", StaticFiles(directory=STATIC / "shared"), name="shared")
app.mount("/kiosk", StaticFiles(directory=STATIC / "kiosk", html=True), name="kiosk")
app.mount("/admin", StaticFiles(directory=STATIC / "admin", html=True), name="admin")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Quartermaster</title>
<body style="font:16px system-ui;background:#10151c;color:#e8edf3;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h1>Quartermaster</h1>
<p><a style="color:#6cb6ff" href="/kiosk/">Kiosk</a> &nbsp;·&nbsp; <a style="color:#6cb6ff" href="/admin/">Admin</a></p></div>"""
