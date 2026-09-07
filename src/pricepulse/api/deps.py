from __future__ import annotations

import hmac
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import Connection

from pricepulse.config import get_settings
from pricepulse.db.engine import get_engine


def get_conn() -> Iterator[Connection]:
    """Read-only connection: Postgres enforces `SET TRANSACTION READ ONLY`."""
    with get_engine().connect().execution_options(postgresql_readonly=True) as conn:
        yield conn


def get_rw_conn() -> Iterator[Connection]:
    """Commit-as-you-go: the route may commit midway (watch request commits before it
    publishes to the outbox); whatever is left is committed after the route returns, and an
    exception in the route rolls back."""
    with get_engine().connect() as conn:
        yield conn
        conn.commit()


def client_ip(request: Request) -> str | None:
    """Viewer address for abuse budgets: the first `X-Forwarded-For` hop (set by CloudFront,
    appended to by API Gateway), else the socket peer (local dev)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().api_key
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")


ReadConn = Annotated[Connection, Depends(get_conn)]
WriteConn = Annotated[Connection, Depends(get_rw_conn)]
