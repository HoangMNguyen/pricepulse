"""Adapter contract. `fetch` does network I/O and returns the verbatim raw payload;
`parse` is pure so it can be unit-tested from fixtures and re-run over stored raw data."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from pricepulse.domain.models import ProductSnapshot


class SourceError(RuntimeError):
    """A retailer returned a structurally invalid or non-ok response."""


class Source(Protocol):
    code: str

    def fetch(self, client: httpx.Client) -> dict[str, Any]: ...

    def parse(self, raw: dict[str, Any]) -> list[ProductSnapshot]: ...


def new_raw_payload(code: str) -> dict[str, Any]:
    return {"source": code, "fetched_at": datetime.now(UTC).isoformat(), "requests": []}
