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
    watch_count: int  # every watch of the address, including this one (unsubscribe-all pages)


_VIEW_SELECT = """
    SELECT w.product_id, w.email, w.min_discount_pct, p.name AS product_name,
           (SELECT count(*) FROM watch x WHERE x.email = w.email) AS watch_count
    FROM w JOIN product p ON p.id = w.product_id
"""


def _view(row: Any) -> WatchView | None:
    if row is None:
        return None
    return WatchView(
        int(row.product_id),
        row.email,
        row.min_discount_pct,
        row.product_name,
        int(row.watch_count),
    )


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


def lock_requests(conn: Connection) -> None:
    """Serialize watch requests for the rest of the transaction so count-then-insert quotas
    cannot be raced past. One global lock: a handful of requests a day, nothing to shard."""
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('watch_request'))"))


def pending_count(conn: Connection, email: str) -> int:
    return int(
        conn.execute(
            text("SELECT count(*) FROM watch WHERE email = :email AND confirmed_at IS NULL"),
            {"email": email},
        ).scalar_one()
    )


def confirmations_last_hour(conn: Connection, client_hash: str | None = None) -> int:
    """Confirmation mails queued in the last hour: across every address (the global budget),
    or for one client when `client_hash` is given."""
    return int(
        conn.execute(
            text(
                """
                SELECT count(*) FROM watch
                WHERE confirmation_sent_at > now() - INTERVAL '1 hour'
                  AND (CAST(:client AS text) IS NULL OR client_hash = :client)
                """
            ),
            {"client": client_hash},
        ).scalar_one()
    )


def insert(
    conn: Connection,
    product_id: int,
    email: str,
    min_discount_pct: Decimal,
    token: str,
    client_hash: str | None,
) -> None:
    """Unconfirmed watch; lets IntegrityError (one watch per product and email) propagate."""
    conn.execute(
        text(
            """
            INSERT INTO watch (product_id, email, min_discount_pct, token, confirmation_sent_at,
                               client_hash)
            VALUES (:product_id, :email, :pct, :token, now(), :client)
            """
        ),
        {
            "product_id": product_id,
            "email": email,
            "pct": min_discount_pct,
            "token": token,
            "client": client_hash,
        },
    )


def pending_token(conn: Connection, product_id: int, email: str) -> tuple[str, bool] | None:
    """(token, resend_allowed) of an existing unconfirmed watch for (product, email), or None
    when there is no watch or it is already confirmed. A resend is allowed once the previous
    confirmation mail is older than 10 minutes."""
    row = conn.execute(
        text(
            """
            SELECT token, confirmation_sent_at < now() - INTERVAL '10 minutes' AS resend_ok
            FROM watch WHERE product_id = :pid AND email = :email AND confirmed_at IS NULL
            """
        ),
        {"pid": product_id, "email": email},
    ).first()
    return None if row is None else (row.token, bool(row.resend_ok))


def touch_confirmation(conn: Connection, token: str, client_hash: str | None) -> None:
    """A confirmation mail is being re-queued for this watch: counts against the budgets."""
    conn.execute(
        text(
            "UPDATE watch SET confirmation_sent_at = now(), client_hash = :client WHERE token = :t"
        ),
        {"t": token, "client": client_hash},
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
              UPDATE watch SET confirmed_at = COALESCE(confirmed_at, now()), client_hash = NULL
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


def delete_all_for_token(conn: Connection, token: str) -> WatchView | None:
    """Delete every watch of the address owning `token`; the view reports how many went."""
    row = conn.execute(
        text(
            """
            WITH w AS (
              DELETE FROM watch
              WHERE email = (SELECT email FROM watch WHERE token = :t)
              RETURNING product_id, email, min_discount_pct, token
            )
            SELECT w.product_id, w.email, w.min_discount_pct, p.name AS product_name,
                   (SELECT count(*) FROM w) AS watch_count
            FROM w JOIN product p ON p.id = w.product_id
            WHERE w.token = :t
            """
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
