from __future__ import annotations

import hmac
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import Connection

from pricepulse.config import get_settings
from pricepulse.db.engine import get_engine


def get_conn() -> Iterator[Connection]:
    """Read-only connection: Postgres enforces `SET TRANSACTION READ ONLY`."""
    with get_engine().connect().execution_options(postgresql_readonly=True) as conn:
        yield conn


def get_rw_conn() -> Iterator[Connection]:
    with get_engine().begin() as conn:
        yield conn


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().api_key
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")


ReadConn = Annotated[Connection, Depends(get_conn)]
WriteConn = Annotated[Connection, Depends(get_rw_conn)]
