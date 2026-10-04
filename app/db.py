from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC 'now'. All timestamps in the DB are naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    """Serialise a naive-UTC datetime so browsers parse it as UTC."""
    return None if dt is None else dt.isoformat(timespec="seconds") + "Z"


def make_engine(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


engine = make_engine(config.DB_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def upgrade_schema() -> None:
    """Add columns that newer versions introduced to a database made by an older one."""
    with engine.begin() as con:
        have = {r[1] for r in con.exec_driver_sql('PRAGMA table_info("products")')}
        if have and "indoor_safe" not in have:
            con.exec_driver_sql("ALTER TABLE products ADD COLUMN indoor_safe BOOLEAN NOT NULL DEFAULT 1")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
