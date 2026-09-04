"""Read queries over `product_price_summary` and history; shared by JSON routes and dashboard."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, text

MAX_LIMIT = 200
SOURCE_CODES = {1: "ikea", 2: "uniqlo"}
SOURCE_IDS = {v: k for k, v in SOURCE_CODES.items()}

_SUMMARY_COLUMNS = """
    s.product_id, s.source_id, s.name, s.category, s.url, s.image_url, s.currency,
    s.current_price, s.current_observed_at, s.retailer_sale_flag, s.retailer_tag, s.valid_to,
    s.list_price, s.reference_price, s.mode_price_90d, s.min_price_90d, s.max_price_90d,
    s.observations_90d, s.discount_pct
"""
_SUMMARY_SELECT = f"SELECT {_SUMMARY_COLUMNS} FROM product_price_summary s"  # noqa: S608 - constant


@dataclass(frozen=True, slots=True)
class DealFilters:
    source: str | None = None
    min_discount: Decimal = Decimal("0")
    flagged_only: bool = False
    q: str | None = None
    limit: int = 50
    cursor: str | None = None


def encode_cursor(discount_pct: Decimal, product_id: int) -> str:
    return base64.urlsafe_b64encode(f"{discount_pct}:{product_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Decimal, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        pct, pid = base64.urlsafe_b64decode(padded).decode().split(":", 1)
        return Decimal(pct), int(pid)
    except (ValueError, binascii.Error, InvalidOperation, UnicodeDecodeError) as exc:
        raise HTTPException(400, "malformed cursor") from exc


def row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row._mapping)
    d["source"] = SOURCE_CODES.get(d.pop("source_id"), "unknown")
    d["is_on_sale"] = bool(d["discount_pct"] > 0 or d["retailer_sale_flag"])
    return d


def list_deals(conn: Connection, f: DealFilters) -> tuple[list[dict[str, Any]], str | None]:
    limit = max(1, min(f.limit, MAX_LIMIT))
    where = ["s.discount_pct >= :min_discount"]
    params: dict[str, Any] = {"min_discount": f.min_discount, "limit": limit + 1}
    if f.source:
        if f.source not in SOURCE_IDS:
            raise HTTPException(400, f"unknown source {f.source!r}")
        where.append("s.source_id = :source_id")
        params["source_id"] = SOURCE_IDS[f.source]
    if f.flagged_only:
        where.append("s.retailer_sale_flag")
    if f.q:
        where.append("s.name ILIKE '%' || :q || '%'")
        params["q"] = f.q
    if f.cursor:
        pct, pid = decode_cursor(f.cursor)
        where.append(
            "(s.discount_pct < :c_pct OR (s.discount_pct = :c_pct AND s.product_id > :c_pid))"
        )
        params.update(c_pct=pct, c_pid=pid)
    sql = (
        f"{_SUMMARY_SELECT} WHERE {' AND '.join(where)} "  # noqa: S608 - fragments are constants
        "ORDER BY s.discount_pct DESC, s.product_id ASC LIMIT :limit"
    )
    rows = conn.execute(text(sql), params).all()
    items = [row_to_dict(r) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit and items:
        last = items[-1]
        next_cursor = encode_cursor(last["discount_pct"], last["product_id"])
    return items, next_cursor


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
            LEFT JOIN product_price_summary s ON s.source_id = src.id
            LEFT JOIN last_run lr ON lr.source_id = src.id
            GROUP BY src.code, lr.started_at, lr.status
            ORDER BY src.code
            """
        )
    ).all()
    return [dict(r._mapping) for r in rows]
