from datetime import datetime

from sqlalchemy import (
    Boolean,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AuthSession(Base):
    """A logged-in browser. Only a hash of the cookie token is stored."""

    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")


class LoginAttempt(Base):
    """Failed-PIN bookkeeping, one row per source IP."""

    __tablename__ = "login_attempts"
    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Caliber(Base):
    __tablename__ = "calibers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    products: Mapped[list["Product"]] = relationship(back_populates="caliber")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    caliber_id: Mapped[int] = mapped_column(ForeignKey("calibers.id"))
    brand: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(120), default="")
    bullet_weight_gr: Mapped[float | None] = mapped_column(Float, nullable=True)
    bullet_type: Mapped[str] = mapped_column(String(40), default="")
    rounds_per_box: Mapped[int] = mapped_column(Integer)
    cost_per_box: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # False = outdoor ranges only; the kiosk asks for confirmation before it is checked out.
    indoor_safe: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    caliber: Mapped[Caliber] = relationship(back_populates="products")
    barcodes: Mapped[list["Barcode"]] = relationship(back_populates="product")


class StockMinimum(Base):
    """Low-stock level, in rounds, for one caliber or one product. No row means no alert."""

    __tablename__ = "stock_minimums"
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)  # 'caliber' | 'product'
    ref_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    min_rounds: Mapped[int] = mapped_column(Integer)


class Barcode(Base):
    """A scannable code. product_id is NULL until the code has been identified."""

    __tablename__ = "barcodes"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    product: Mapped[Product | None] = relationship(back_populates="barcodes")


class BarcodePhoto(Base):
    """A picture of the box for one code. Kept in its own table so listings never load the bytes."""

    __tablename__ = "barcode_photos"
    code: Mapped[str] = mapped_column(ForeignKey("barcodes.code"), primary_key=True)
    image: Mapped[bytes] = mapped_column(LargeBinary)  # JPEG, longest side <= 1280 px
    thumb: Mapped[bytes] = mapped_column(LargeBinary)  # JPEG, longest side <= 240 px
    etag: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UpcLookup(Base):
    """Cached answer from the online UPC lookup, so each code is only ever asked about once."""

    __tablename__ = "upc_lookups"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(String(500), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Batch(Base):
    """One ammo in / ammo out session. Items are queued here until finished."""

    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # 'in' | 'out'
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft|finished|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["BatchItem"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="BatchItem.id"
    )


class BatchItem(Base):
    __tablename__ = "batch_items"
    __table_args__ = (UniqueConstraint("batch_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"))
    code: Mapped[str] = mapped_column(ForeignKey("barcodes.code"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    batch: Mapped[Batch] = relationship(back_populates="items")


class Transaction(Base):
    """Append-only ledger. Inventory is the sum of `boxes` per code."""

    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    code: Mapped[str] = mapped_column(ForeignKey("barcodes.code"), index=True)
    boxes: Mapped[int] = mapped_column(Integer)  # signed: + in, - out
    kind: Mapped[str] = mapped_column(String(8))  # in | out | adjust
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
