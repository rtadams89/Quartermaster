"""Download a backup of everything, and restore from one.

A backup is a SQLite file holding all inventory data: calibers, products, barcodes, box photos,
the full transaction history, the label counter and preferences. It deliberately leaves out:

* the PIN hash (a 4-digit PIN's hash can be brute-forced in milliseconds, so a backup file that
  contained it would effectively contain the PIN), and
* login sessions and lockout counters (meaningless on another server).

Restoring replaces all inventory data with the backup's contents and keeps the current PIN,
the current login sessions and the lockout counters.
"""
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import config, models, security  # noqa: F401  (models import registers every table)
from ..db import Base, SessionLocal, engine, get_db
from ..seed import seed
from .auth import _too_many  # same lockout response as the PIN change

router = APIRouter(prefix="/api", dependencies=[Depends(security.require_auth)])

EPHEMERAL_TABLES = {"auth_sessions", "login_attempts"}
# Columns added after a backup was made are filled with their default when it is restored.
OPTIONAL_COLUMNS = {("products", "indoor_safe")}
CORE_TABLES = {"calibers", "products", "barcodes", "transactions"}
MAX_RESTORE_BYTES = 1024 * 1024 * 1024
KEEP_SAFETY_COPIES = 5


def _data_dir() -> Path:
    return Path(config.DB_PATH).resolve().parent


def _unlink(path: str | Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(str(path) + suffix)
        except FileNotFoundError:
            pass


def _snapshot(dest: Path) -> None:
    """Consistent copy of the live database (safe while the app is in use)."""
    src = sqlite3.connect(config.DB_PATH)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _make_self_contained(path: Path, vacuum: bool = False) -> None:
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=DELETE")  # one file, no -wal/-shm companions
        if vacuum:
            con.execute("VACUUM")
    finally:
        con.close()


# ------------------------------------------------------------------- backup
@router.get("/backup")
def download_backup():
    fd, tmp = tempfile.mkstemp(prefix="backup-", suffix=".db", dir=_data_dir())
    os.close(fd)
    try:
        _snapshot(Path(tmp))
        con = sqlite3.connect(tmp)
        try:
            for table in EPHEMERAL_TABLES:
                con.execute(f'DELETE FROM "{table}"')
            con.execute("DELETE FROM settings WHERE key = ?", (security.PIN_KEY,))
            con.commit()
        finally:
            con.close()
        _make_self_contained(Path(tmp), vacuum=True)
    except Exception:
        _unlink(tmp)
        raise
    name = f"quartermaster-backup-{datetime.now():%Y%m%d-%H%M%S}.db"
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=name,
        background=BackgroundTask(_unlink, tmp),
    )


# ------------------------------------------------------------------ restore
def _validate(path: Path) -> dict:
    """Check the uploaded file really is a compatible Quartermaster backup; return row counts."""
    not_backup = HTTPException(400, "That file isn't a Quartermaster backup")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        raise not_backup
    try:
        try:
            check = con.execute("PRAGMA integrity_check").fetchone()
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            raise not_backup
        if not check or check[0] != "ok":
            raise HTTPException(400, "That backup file is damaged")
        if not CORE_TABLES <= tables:
            raise not_backup
        for t in Base.metadata.sorted_tables:
            if t.name in EPHEMERAL_TABLES:
                continue
            if t.name not in tables:
                raise HTTPException(400, f"That backup is from an incompatible version (table '{t.name}' is missing)")
            have = {r[1] for r in con.execute(f'PRAGMA table_info("{t.name}")')}
            missing = {c.name for c in t.columns if (t.name, c.name) not in OPTIONAL_COLUMNS} - have
            if missing:
                raise HTTPException(
                    400, f"That backup is from an incompatible version ('{t.name}' is missing {sorted(missing)})"
                )
        return {
            name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in ("calibers", "products", "barcodes", "transactions", "barcode_photos")
        }
    finally:
        con.close()


def _safety_copy(kind: str = "restore") -> Path:
    bdir = _data_dir() / "backups"
    bdir.mkdir(exist_ok=True)
    dest = bdir / f"pre-{kind}-{datetime.now():%Y%m%d-%H%M%S}.db"
    _snapshot(dest)
    _make_self_contained(dest)
    for old in sorted(bdir.glob(f"pre-{kind}-*.db"))[:-KEEP_SAFETY_COPIES]:
        _unlink(old)
    return dest


def _apply(backup: Path) -> None:
    """Replace the data in the live database, atomically, with the backup's contents."""
    con = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("ATTACH DATABASE ? AS bk", (str(backup),))
        tables = [t for t in Base.metadata.sorted_tables if t.name not in EPHEMERAL_TABLES]
        con.execute("BEGIN IMMEDIATE")
        try:
            for t in reversed(tables):  # children before parents
                if t.name == "settings":
                    con.execute('DELETE FROM main."settings" WHERE key != ?', (security.PIN_KEY,))
                else:
                    con.execute(f'DELETE FROM main."{t.name}"')
            for t in tables:  # parents before children
                inbk = {r[1] for r in con.execute(f'PRAGMA bk.table_info("{t.name}")')}
                cols = ", ".join(f'"{c.name}"' for c in t.columns if c.name in inbk)
                sql = f'INSERT INTO main."{t.name}" ({cols}) SELECT {cols} FROM bk."{t.name}"'
                if t.name == "settings":
                    con.execute(sql + " WHERE key != ?", (security.PIN_KEY,))
                else:
                    con.execute(sql)
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise HTTPException(400, "That backup is internally inconsistent, so nothing was changed")
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("DETACH DATABASE bk")
    finally:
        con.close()


@router.post("/restore")
async def restore(request: Request):
    """Body is the raw backup file."""
    fd, tmp = tempfile.mkstemp(prefix="restore-", suffix=".db", dir=_data_dir())
    os.close(fd)
    try:
        size = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_RESTORE_BYTES:
                    raise HTTPException(413, "That file is too large to be a backup")
                f.write(chunk)
        if not size:
            raise HTTPException(400, "No file received")
        counts = _validate(Path(tmp))
        safety = _safety_copy()
        try:
            _apply(Path(tmp))
        except HTTPException:
            raise
        except sqlite3.Error as e:
            raise HTTPException(500, f"Restore failed and nothing was changed ({e})")
        engine.dispose()
        return {"ok": True, "restored": counts, "safety_copy": f"backups/{safety.name}"}
    finally:
        _unlink(tmp)


# -------------------------------------------------------------------- reset
class ResetIn(BaseModel):
    pin: str = Field(pattern=r"^\d{4}$")
    confirm: str


@router.post("/reset")
def reset_all(body: ResetIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Put the system back to a fresh install: every table is emptied, including the PIN, all logins and
    the lockout counters, and the starter caliber list is put back. The next visitor sets a new PIN.

    Needs the current PIN plus the typed word RESET. No copy of the data is kept; download a backup first."""
    if body.confirm != "RESET":
        raise HTTPException(400, "Type RESET to confirm")
    ip = security.client_ip(request)
    wait = security.lock_remaining(db, ip)
    if wait:
        raise _too_many(wait)
    stored = security.get_pin_hash(db)
    if not stored or not security.verify_pin_hash(body.pin, stored):
        lock = security.record_failure(db, ip)
        if lock:
            raise _too_many(lock)
        raise HTTPException(400, "Incorrect PIN")
    db.close()

    con = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN IMMEDIATE")
        try:
            for t in reversed(Base.metadata.sorted_tables):  # children before parents
                con.execute(f'DELETE FROM "{t.name}"')
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    except sqlite3.Error as e:
        raise HTTPException(500, f"Reset failed and nothing was changed ({e})")
    finally:
        con.close()
    with SessionLocal() as s:
        seed(s)
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}
