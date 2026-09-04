"""Public watches: create -> outbox message -> confirm link -> alerts -> unsubscribe link."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricepulse.api.app import create_app
from pricepulse.api.routes.watches import MAX_UNCONFIRMED_PER_EMAIL
from pricepulse.config import Settings
from pricepulse.db.repo import watches_for
from tests.integration.test_api import HEADERS, seed


@pytest.fixture
def client(
    conn: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    seed(conn)
    monkeypatch.setattr("pricepulse.api.deps.get_engine", lambda: conn)
    monkeypatch.setattr("pricepulse.api.deps.get_settings", lambda: settings)
    with TestClient(create_app()) as c:
        yield c


def _outbox(settings: Settings) -> list[Path]:
    return sorted((Path(settings.raw_local_dir) / "outbox" / "watch_confirm").rglob("*.json"))


def _product_ids(client: TestClient) -> list[int]:
    items = client.get("/v1/deals", params={"limit": 200, "sort": "name"}).json()["items"]
    return [i["product_id"] for i in items]


def test_double_opt_in_lifecycle(client: TestClient, conn: Engine, settings: Settings) -> None:
    pid = _product_ids(client)[0]
    body = {"product_id": pid, "email": "w@example.com", "min_discount_pct": 10}
    assert client.post("/v1/watches", json=body).status_code == 202

    files = _outbox(settings)
    assert len(files) == 1
    message = json.loads(files[0].read_text())
    assert message["kind"] == "watch_confirm" and message["email"] == "w@example.com"
    assert message["product_id"] == pid and message["min_discount_pct"] == "10"
    token = message["token"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{40,}", token)

    with conn.connect() as c:
        row = c.execute(
            text("SELECT token, confirmed_at, confirmation_sent_at FROM watch"), {}
        ).one()
        assert row.token == token and row.confirmed_at is None
        assert row.confirmation_sent_at is not None
        assert watches_for(c, [pid]) == {}  # unconfirmed watches never fire

    page = client.get(f"/watches/confirm/{token}")
    assert page.status_code == 200 and "Watch confirmed" in page.text
    assert "w@example.com" in page.text
    with conn.connect() as c:
        confirmed_at = c.execute(text("SELECT confirmed_at FROM watch")).scalar_one()
        assert confirmed_at is not None
        rows = watches_for(c, [pid])[pid]
        assert len(rows) == 1 and rows[0].email == "w@example.com" and rows[0].token == token

    again = client.get(f"/watches/confirm/{token}")  # idempotent
    assert again.status_code == 200
    with conn.connect() as c:
        assert c.execute(text("SELECT confirmed_at FROM watch")).scalar_one() == confirmed_at

    bye = client.get(f"/watches/unsubscribe/{token}")
    assert bye.status_code == 200 and "Unsubscribed" in bye.text
    with conn.connect() as c:
        assert c.execute(text("SELECT count(*) FROM watch")).scalar_one() == 0
    assert client.get(f"/watches/unsubscribe/{token}").status_code == 404
    assert client.get("/watches/confirm/nope").status_code == 404
    assert "Link not found" in client.get("/watches/confirm/nope").text


def test_unconfirmed_watches_are_rate_limited_per_email(client: TestClient) -> None:
    ids = _product_ids(client)
    for pid in ids[:MAX_UNCONFIRMED_PER_EMAIL]:
        r = client.post("/v1/watches", json={"product_id": pid, "email": "spam@example.com"})
        assert r.status_code == 202
    sixth = client.post(
        "/v1/watches",
        json={"product_id": ids[MAX_UNCONFIRMED_PER_EMAIL], "email": "spam@example.com"},
    )
    assert sixth.status_code == 429
    assert sixth.json()["detail"] == "too many unconfirmed watches for this email"
    other = client.post("/v1/watches", json={"product_id": ids[0], "email": "other@example.com"})
    assert other.status_code == 202
    listed = client.get("/v1/watches", params={"email": "spam@example.com"}, headers=HEADERS).json()
    assert len(listed) == MAX_UNCONFIRMED_PER_EMAIL


def test_duplicate_watch_is_409_with_hint(client: TestClient) -> None:
    pid = _product_ids(client)[0]
    body = {"product_id": pid, "email": "w@example.com"}
    assert client.post("/v1/watches", json=body).status_code == 202
    dup = client.post("/v1/watches", json=body)
    assert dup.status_code == 409 and "check your inbox" in dup.json()["detail"]
