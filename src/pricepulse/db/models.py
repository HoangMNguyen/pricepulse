"""SQLAlchemy 2.0 declarative models.

The schema itself lives in alembic/versions (hand-written SQL); these classes exist for
typed Core queries and are never used to emit DDL."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "source"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)


class Product(Base):
    __tablename__ = "product"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("source.id"))
    external_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IngestionRun(Base):
    __tablename__ = "ingestion_run"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("source.id"))
    raw_object_key: Mapped[str] = mapped_column(Text, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    products_seen: Mapped[int | None] = mapped_column(Integer)
    observations_inserted: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class PriceObservation(Base):
    __tablename__ = "price_observation"
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product.id"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ingestion_run.id"))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    list_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    retailer_sale_flag: Mapped[bool] = mapped_column(Boolean)
    retailer_tag: Mapped[str | None] = mapped_column(Text)
    valid_to: Mapped[date | None] = mapped_column(Date)


class Watch(Base):
    __tablename__ = "watch"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product.id"))
    email: Mapped[str] = mapped_column(Text)
    min_discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 1), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Alert(Base):
    __tablename__ = "alert"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ingestion_run.id"))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product.id"))
    kind: Mapped[str] = mapped_column(Text)
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    new_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Read-only materialized view; refreshed by the ingestion service after every run.
product_price_summary = Table(
    "product_price_summary",
    Base.metadata,
    Column("product_id", BigInteger, primary_key=True),
    Column("source_id", SmallInteger),
    Column("name", Text),
    Column("category", Text),
    Column("url", Text),
    Column("image_url", Text),
    Column("currency", String(3)),
    Column("current_price", Numeric(10, 2)),
    Column("current_observed_at", DateTime(timezone=True)),
    Column("retailer_sale_flag", Boolean),
    Column("retailer_tag", Text),
    Column("valid_to", Date),
    Column("list_price", Numeric(10, 2)),
    Column("reference_price", Numeric(10, 2)),
    Column("mode_price_90d", Numeric(10, 2)),
    Column("min_price_90d", Numeric(10, 2)),
    Column("max_price_90d", Numeric(10, 2)),
    Column("observations_90d", BigInteger),
    Column("discount_pct", Numeric(5, 1)),
    info={"is_view": True},
)
