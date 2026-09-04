"""Polite HTTP: one client, explicit User-Agent, bounded retries, fixed pause between requests."""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from pricepulse.config import Settings

PAUSE_SECONDS = 0.5
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def make_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        timeout=20,
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        follow_redirects=False,
    )


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _RETRY_STATUSES


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_retryable),
    reraise=True,
)
def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> httpx.Response:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response


def get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> tuple[str, int, Any]:
    """GET with retries; returns (final url, status, decoded body). Sleeps after every request."""
    try:
        response = _get(client, url, params)
    finally:
        time.sleep(PAUSE_SECONDS)
    return str(response.url), response.status_code, response.json()
