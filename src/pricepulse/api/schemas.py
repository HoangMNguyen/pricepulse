from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

SortKey = Literal["discount", "savings", "price_asc", "price_desc", "name", "newest", "ending_soon"]


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    source: str
    name: str
    category: str | None
    url: str
    image_url: str | None
    currency: str
    current_price: Decimal
    reference_price: Decimal | None
    list_price: Decimal | None
    discount_pct: Decimal
    retailer_sale_flag: bool
    retailer_tag: str | None
    valid_to: date | None
    is_on_sale: bool
    current_observed_at: datetime
    first_seen_at: datetime
    is_new: bool
    previous_price: Decimal | None
    previous_observed_at: datetime | None
    drop_vs_previous_pct: Decimal
    savings: Decimal
    days_left: int | None


class ProductOut(DealOut):
    mode_price_90d: Decimal | None
    min_price_90d: Decimal | None
    max_price_90d: Decimal | None
    observations_90d: int


class DealsPage(BaseModel):
    items: list[DealOut]
    next_cursor: str | None
    total: int


class CategoryOut(BaseModel):
    source: str
    category: str
    products: int


class HistoryPoint(BaseModel):
    observed_at: datetime
    price: Decimal
    list_price: Decimal | None
    retailer_sale_flag: bool


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    raw_object_key: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    products_seen: int | None
    observations_inserted: int | None
    error: str | None


class SourceStats(BaseModel):
    source: str
    products: int
    on_sale: int
    last_run_at: datetime | None
    last_run_status: str | None


class WatchIn(BaseModel):
    product_id: int
    email: EmailStr
    min_discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class WatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    email: str
    min_discount_pct: Decimal
    created_at: datetime
    confirmed_at: datetime | None


class WatchPendingOut(BaseModel):
    email: str
    product_id: int
    min_discount_pct: Decimal


class Health(BaseModel):
    status: str
    db: str
    version: str
