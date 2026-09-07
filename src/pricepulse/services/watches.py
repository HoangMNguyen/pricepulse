"""Watch workflow: request (double opt-in) lives here; confirm/unsubscribe are single
statements in `pricepulse.db.watches` and are called from the dashboard routes directly.

Request ordering: the watch row is committed first, then the confirmation message is
published to the outbox. A crash between the two leaves an unconfirmed row without mail;
the next request for the same (product, email) re-queues the confirmation (rate-limited) instead
of answering 409, so that window heals itself. The reverse order would mail a token that never
commits, and nothing could heal that."""

from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from pricepulse.db import watches
from pricepulse.storage.outbox import Outbox

MAX_UNCONFIRMED_PER_EMAIL = 5
# Confirmation mails per hour. Real traffic is a few sign-ups a day; these bound what a script
# can make SES send to arbitrary inboxes, whatever addresses it invents: one budget per client
# (hashed IP) and one across everyone.
MAX_CONFIRMATIONS_PER_CLIENT_PER_HOUR = 10
MAX_CONFIRMATIONS_PER_HOUR = 30


class WatchError(Exception):
    """Base for request_watch failures; routes map subclasses to HTTP statuses."""


class ProductNotFoundError(WatchError): ...


class TooManyPendingError(WatchError): ...


class TooManyRequestsError(WatchError): ...


class AlreadyWatchingError(WatchError): ...


def client_hash(client: str | None) -> str | None:
    """SHA-256 of the client identifier (viewer IP). Only unconfirmed rows carry it."""
    return hashlib.sha256(client.encode()).hexdigest() if client else None


def request_watch(
    conn: Connection,
    outbox: Outbox,
    product_id: int,
    email: str,
    min_discount_pct: Decimal,
    client: str | None = None,
) -> str:
    """Insert an unconfirmed watch, commit, then queue its confirmation email; returns the
    token. A pending duplicate re-queues its confirmation (once per 10 minutes) and returns
    the existing token; a confirmed duplicate raises AlreadyWatchingError. Quotas are checked
    under a transaction-scoped lock, so concurrent requests cannot both pass a count that only
    one of them should."""
    product = watches.product_for_watch(conn, product_id)
    if product is None:
        raise ProductNotFoundError(product_id)
    hashed = client_hash(client)
    watches.lock_requests(conn)
    pending = watches.pending_token(conn, product_id, email)
    if pending is not None:
        token, resend_ok = pending
        if not resend_ok:
            return token  # a confirmation went out minutes ago; nothing to add
    if watches.confirmations_last_hour(conn) >= MAX_CONFIRMATIONS_PER_HOUR or (
        hashed
        and watches.confirmations_last_hour(conn, hashed) >= MAX_CONFIRMATIONS_PER_CLIENT_PER_HOUR
    ):
        raise TooManyRequestsError()
    if pending is not None:
        watches.touch_confirmation(conn, token, hashed)
    else:
        if watches.pending_count(conn, email) >= MAX_UNCONFIRMED_PER_EMAIL:
            raise TooManyPendingError(email)
        token = secrets.token_urlsafe(32)
        try:
            with conn.begin_nested():
                watches.insert(conn, product_id, email, min_discount_pct, token, hashed)
        except IntegrityError as exc:
            raise AlreadyWatchingError(product_id) from exc
    conn.commit()
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
