"""Read queries over `product_price_summary` and history; shared by JSON routes and dashboard."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, text

from pricepulse.domain.models import LABELS
from pricepulse.domain.pricing import discount_pct

MAX_LIMIT = 200

# key -> (column, direction). Dict order is the UI order.
SORTS: dict[str, tuple[str, str]] = {
    "discount": ("s.discount_pct", "DESC"),
    "savings": ("s.savings", "DESC"),
    "price_asc": ("s.current_price", "ASC"),
    "price_desc": ("s.current_price", "DESC"),
    "name": ("s.name", "ASC"),
    "newest": ("s.first_seen_at", "DESC"),
    "ending_soon": ("s.valid_to", "ASC"),  # implies s.valid_to IS NOT NULL
}
DEFAULT_SORT = "discount"
SORT_LABELS = {
    "discount": "Biggest % off",
    "savings": "Biggest $ saving",
    "price_asc": "Price: low to high",
    "price_desc": "Price: high to low",
    "name": "Name",
    "newest": "Newest",
    "ending_soon": "Ending soon",
}
# Display text per label, in LABELS order (the toolbar select and the row pills).
LABEL_NAMES = {
    "last_chance": "last chance",
    "in_store_only": "in-store only",
    "xl_store_only": "XL stores only",
    "online_only": "online only",
    "select_variants": "select colours/sizes",
    "coming_soon": "coming soon",
}

_SUMMARY_COLUMNS = """
    s.product_id, s.source, s.name, s.category, s.url, s.image_url, s.currency,
    s.first_seen_at, s.last_seen_at,
    s.current_price, s.current_observed_at, s.retailer_sale_flag, s.retailer_tag, s.valid_to,
    s.list_price, s.reference_price, s.mode_price_90d, s.min_price_90d, s.max_price_90d,
    s.observations_90d, s.discount_pct, s.previous_price, s.previous_observed_at, s.savings,
    s.is_current, p.variants, p.labels
"""
# PK join: variants/labels are current-state columns kept out of the materialized view.
_SUMMARY_FROM = "FROM product_price_summary s JOIN product p ON p.id = s.product_id"
_SUMMARY_SELECT = f"SELECT {_SUMMARY_COLUMNS} {_SUMMARY_FROM}"  # noqa: S608 - constant


@dataclass(frozen=True, slots=True)
class DealFilters:
    source: str | None = None
    min_discount: Decimal = Decimal("0")
    flagged_only: bool = False
    on_sale_only: bool = False
    category: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    q: str | None = None
    label: str | None = None
    size: str | None = None
    sort: str = DEFAULT_SORT
    limit: int = 50
    cursor: str | None = None


def encode_cursor(sort: str, value: Any, product_id: int) -> str:
    raw = json.dumps({"s": sort, "v": str(value), "id": product_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _parse_cursor_value(sort: str, value: str) -> Any:
    if sort in ("discount", "savings", "price_asc", "price_desc"):
        return Decimal(value)
    if sort == "newest":
        return datetime.fromisoformat(value)
    if sort == "ending_soon":
        return date.fromisoformat(value)
    return value  # name


def decode_cursor(cursor: str, sort: str) -> tuple[Any, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        cursor_sort, value, pid = data["s"], data["v"], int(data["id"])
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(400, "malformed cursor") from exc
    if cursor_sort != sort:
        raise HTTPException(400, "cursor does not match sort")
    try:
        return _parse_cursor_value(sort, value), pid
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise HTTPException(400, "malformed cursor") from exc


def row_to_dict(row: Any, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    d = dict(row._mapping)
    d["is_on_sale"] = bool(d["discount_pct"] > 0 or d["retailer_sale_flag"])
    d["is_new"] = d["first_seen_at"] >= now - timedelta(hours=24)
    d["drop_vs_previous_pct"] = discount_pct(d["current_price"], d.get("previous_price"))
    d["days_left"] = (d["valid_to"] - now.date()).days if d.get("valid_to") else None
    return d


def _jsonb_where(f: DealFilters, where: list[str], params: dict[str, Any]) -> None:
    """Membership filters on the `product` JSONB columns (containment, GIN-friendly)."""
    if f.label:
        if f.label not in LABELS:
            raise HTTPException(422, f"unknown label; expected one of {', '.join(LABELS)}")
        where.append("p.labels @> CAST(:label AS jsonb)")
        params["label"] = json.dumps([f.label])
    if f.size:  # partial-object containment; an unknown size matches nothing (routes cap length)
        where.append("p.variants @> CAST(:size AS jsonb)")
        params["size"] = json.dumps({"sizes": [{"name": f.size, "in_stock": True}]})


def _where(f: DealFilters, with_cursor: bool) -> tuple[list[str], dict[str, Any]]:
    if f.sort not in SORTS:
        raise HTTPException(400, "unknown sort")
    where = ["s.is_current", "s.discount_pct >= :min_discount"]
    params: dict[str, Any] = {"min_discount": f.min_discount}
    if f.source:  # an unknown code matches nothing
        where.append("s.source = :source")
        params["source"] = f.source
    if f.flagged_only:
        where.append("s.retailer_sale_flag")
    if f.on_sale_only:
        where.append("(s.discount_pct > 0 OR s.retailer_sale_flag)")
    if f.category:
        where.append("s.category = :category")
        params["category"] = f.category
    if f.min_price is not None:
        where.append("s.current_price >= :min_price")
        params["min_price"] = f.min_price
    if f.max_price is not None:
        where.append("s.current_price <= :max_price")
        params["max_price"] = f.max_price
    if f.q:
        where.append("s.name ILIKE '%' || :q || '%'")
        params["q"] = f.q
    _jsonb_where(f, where, params)
    if f.sort == "ending_soon":
        where.append("s.valid_to IS NOT NULL")
    if with_cursor and f.cursor:
        value, pid = decode_cursor(f.cursor, f.sort)
        col, direction = SORTS[f.sort]
        op = "<" if direction == "DESC" else ">"
        where.append(f"({col} {op} :c_v OR ({col} = :c_v AND s.product_id > :c_id))")
        params.update(c_v=value, c_id=pid)
    return where, params


def list_deals(
    conn: Connection, f: DealFilters, *, with_total: bool = True
) -> tuple[list[dict[str, Any]], str | None, int | None]:
    """One page of deals plus the next keyset cursor and, unless `with_total` is off (HTMX
    "load more" discards it), the total matching count."""
    limit = max(1, min(f.limit, MAX_LIMIT))
    where, params = _where(f, with_cursor=True)  # validates sort and cursor
    col, direction = SORTS[f.sort]
    total = None
    if with_total:
        count_where, count_params = _where(f, with_cursor=False)
        count_sql = f"SELECT count(*) {_SUMMARY_FROM} WHERE "  # noqa: S608 - constant
        count_sql += " AND ".join(count_where)
        total = int(conn.execute(text(count_sql), count_params).scalar_one())
    sql = (
        f"{_SUMMARY_SELECT} WHERE {' AND '.join(where)} "  # noqa: S608 - fragments are constants
        f"ORDER BY {col} {direction}, s.product_id ASC LIMIT :limit"
    )
    rows = conn.execute(text(sql), {**params, "limit": limit + 1}).all()
    now = datetime.now(UTC)
    items = [row_to_dict(r, now) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit and items:
        last = rows[limit - 1]._mapping
        next_cursor = encode_cursor(f.sort, last[col.removeprefix("s.")], last["product_id"])
    return items, next_cursor, total


def get_product(conn: Connection, product_id: int) -> dict[str, Any]:
    row = conn.execute(
        text(f"{_SUMMARY_SELECT} WHERE s.product_id = :id"), {"id": product_id}
    ).first()
    if row is None:
        raise HTTPException(404, "product not found")
    return row_to_dict(row)


def get_history(conn: Connection, product_id: int, days: int) -> list[dict[str, Any]]:
    days = max(1, min(days, 730))
    rows = conn.execute(
        text(
            """
            SELECT observed_at, price, list_price, retailer_sale_flag
            FROM price_observation
            WHERE product_id = :id AND observed_at >= now() - make_interval(days => :days)
            ORDER BY observed_at
            """
        ),
        {"id": product_id, "days": days},
    ).all()
    return [dict(r._mapping) for r in rows]


def categories(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT s.source, s.category, count(*) AS products
            FROM product_price_summary s
            WHERE s.is_current AND s.category IS NOT NULL
            GROUP BY 1, 2 ORDER BY 1, 2
            """
        )
    ).all()
    return [{"source": r.source, "category": r.category, "products": int(r.products)} for r in rows]


def sizes(conn: Connection, source: str) -> list[str]:
    """Size names with at least one in-stock current product, in retailer (displayCode) order.
    Empty for retailers without size data (IKEA)."""
    rows = conn.execute(
        text(
            """
            SELECT s->>'name' AS name, min(s->>'code') AS code
            FROM product_price_summary ps
            JOIN product p ON p.id = ps.product_id, jsonb_array_elements(p.variants->'sizes') s
            WHERE ps.is_current AND ps.source = :source AND (s->>'in_stock') = 'true'
            GROUP BY 1 ORDER BY 2, 1
            """
        ),
        {"source": source},
    ).all()
    return [r.name for r in rows]


def sitemap_entries(conn: Connection) -> list[tuple[int, datetime]]:
    rows = conn.execute(
        text(
            "SELECT product_id, current_observed_at FROM product_price_summary "
            "WHERE is_current ORDER BY product_id"
        )
    ).all()
    return [(int(r.product_id), r.current_observed_at) for r in rows]


def list_runs(conn: Connection, limit: int) -> list[dict[str, Any]]:
    limit = max(1, min(limit, MAX_LIMIT))
    rows = conn.execute(
        text(
            """
            SELECT r.id, s.code AS source, r.raw_object_key, r.status, r.started_at,
                   r.finished_at, r.products_seen, r.observations_inserted, r.error
            FROM ingestion_run r JOIN source s ON s.id = r.source_id
            ORDER BY r.id DESC LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()
    return [dict(r._mapping) for r in rows]


def stats(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            WITH last_run AS (
              SELECT DISTINCT ON (source_id) source_id, started_at, status
              FROM ingestion_run ORDER BY source_id, id DESC
            )
            SELECT src.code AS source,
                   count(s.product_id) AS products,
                   count(s.product_id) FILTER (WHERE s.discount_pct > 0 OR s.retailer_sale_flag)
                     AS on_sale,
                   lr.started_at AS last_run_at, lr.status AS last_run_status
            FROM source src
            LEFT JOIN product_price_summary s ON s.source_id = src.id AND s.is_current
            LEFT JOIN last_run lr ON lr.source_id = src.id
            GROUP BY src.code, lr.started_at, lr.status
            ORDER BY src.code
            """
        )
    ).all()
    return [dict(r._mapping) for r in rows]
