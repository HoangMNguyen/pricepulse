"""Watch workflow: request (double opt-in) lives here; confirm/unsubscribe are single
statements in `pricepulse.db.watches` and are called from the dashboard routes directly."""

from __future__ import annotations

import secrets
from decimal import Decimal

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from pricepulse.db import watches
from pricepulse.storage.outbox import Outbox

MAX_UNCONFIRMED_PER_EMAIL = 5


class WatchError(Exception):
    """Base for request_watch failures; routes map subclasses to HTTP statuses."""


class ProductNotFoundError(WatchError): ...


class TooManyPendingError(WatchError): ...


class AlreadyWatchingError(WatchError): ...


def request_watch(
    conn: Connection, outbox: Outbox, product_id: int, email: str, min_discount_pct: Decimal
) -> str:
    """Insert an unconfirmed watch and queue its confirmation email; returns the token."""
    product = watches.product_for_watch(conn, product_id)
    if product is None:
        raise ProductNotFoundError(product_id)
    if watches.pending_count(conn, email) >= MAX_UNCONFIRMED_PER_EMAIL:
        raise TooManyPendingError(email)
    token = secrets.token_urlsafe(32)
    try:
        with conn.begin_nested():
            watches.insert(conn, product_id, email, min_discount_pct, token)
    except IntegrityError as exc:
        raise AlreadyWatchingError(product_id) from exc
    name, url = product
    outbox.put(
        {
            "kind": "watch_confirm",
            "email": email,
            "product_id": product_id,
            "product_name": name,
            "product_url": url,
            "min_discount_pct": str(min_discount_pct),
            "token": token,
        }
    )
    return token
