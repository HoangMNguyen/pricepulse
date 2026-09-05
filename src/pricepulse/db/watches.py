"""Watch SQL: `Connection` in, plain values out. Workflow lives in `services.watches`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text


@dataclass(frozen=True, slots=True)
class WatchRow:
    product_id: int
    email: str
    min_discount_pct: Decimal
    token: str


@dataclass(frozen=True, slots=True)
class WatchView:
    product_id: int
    email: str
    min_discount_pct: Decimal
    product_name: str


_VIEW_SELECT = """
    SELECT w.product_id, w.email, w.min_discount_pct, p.name AS product_name
    FROM w JOIN product p ON p.id = w.product_id
"""


def _view(row: Any) -> WatchView | None:
    if row is None:
        return None
    return WatchView(int(row.product_id), row.email, row.min_discount_pct, row.product_name)


def watches_for(conn: Connection, product_ids: Iterable[int]) -> dict[int, list[WatchRow]]:
    """Confirmed watches only: unconfirmed rows never produce mail."""
    rows = conn.execute(
        text(
            "SELECT product_id, email, min_discount_pct, token FROM watch "
            "WHERE product_id = ANY(:ids) AND confirmed_at IS NOT NULL"
        ),
        {"ids": list(product_ids)},
    )
    out: dict[int, list[WatchRow]] = {}
    for r in rows:
        out.setdefault(int(r.product_id), []).append(
            WatchRow(int(r.product_id), r.email, r.min_discount_pct, r.token)
        )
    return out


def product_for_watch(conn: Connection, product_id: int) -> tuple[str, str] | None:
    """(name, url) of the product, or None."""
    row = conn.execute(
        text("SELECT name, url FROM product WHERE id = :id"), {"id": product_id}
    ).first()
    return None if row is None else (row.name, row.url)


def pending_count(conn: Connection, email: str) -> int:
    return int(
        conn.execute(
            text("SELECT count(*) FROM watch WHERE email = :email AND confirmed_at IS NULL"),
            {"email": email},
        ).scalar_one()
    )


def insert(
    conn: Connection, product_id: int, email: str, min_discount_pct: Decimal, token: str
) -> None:
    """Unconfirmed watch; lets IntegrityError (one watch per product and email) propagate."""
    conn.execute(
        text(
            """
            INSERT INTO watch (product_id, email, min_discount_pct, token, confirmation_sent_at)
            VALUES (:product_id, :email, :pct, :token, now())
            """
        ),
        {"product_id": product_id, "email": email, "pct": min_discount_pct, "token": token},
    )


def by_token(conn: Connection, token: str) -> WatchView | None:
    row = conn.execute(
        text(f"WITH w AS (SELECT * FROM watch WHERE token = :t) {_VIEW_SELECT}"),  # noqa: S608 - constant fragment
        {"t": token},
    ).first()
    return _view(row)


def confirm(conn: Connection, token: str) -> WatchView | None:
    """Idempotent: a confirmed watch keeps its original confirmed_at."""
    row = conn.execute(
        text(
            f"""
            WITH w AS (
              UPDATE watch SET confirmed_at = COALESCE(confirmed_at, now())
              WHERE token = :t RETURNING product_id, email, min_discount_pct
            ) {_VIEW_SELECT}
            """  # noqa: S608 - constant fragment
        ),
        {"t": token},
    ).first()
    return _view(row)


def delete_by_token(conn: Connection, token: str) -> WatchView | None:
    row = conn.execute(
        text(
            f"""
            WITH w AS (
              DELETE FROM watch WHERE token = :t RETURNING product_id, email, min_discount_pct
            ) {_VIEW_SELECT}
            """  # noqa: S608 - constant fragment
        ),
        {"t": token},
    ).first()
    return _view(row)


def list_for_email(conn: Connection, email: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT id, product_id, email, min_discount_pct, created_at, confirmed_at FROM watch "
            "WHERE email = :email ORDER BY id"
        ),
        {"email": email},
    ).all()
    return [dict(r._mapping) for r in rows]


def delete_by_id(conn: Connection, watch_id: int) -> bool:
    return bool(conn.execute(text("DELETE FROM watch WHERE id = :id"), {"id": watch_id}).rowcount)
