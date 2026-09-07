"""Public watches: create -> outbox message -> confirm link -> alerts -> unsubscribe link."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricepulse.config import Settings
from pricepulse.db.watches import watches_for
from pricepulse.services.watches import (
    MAX_CONFIRMATIONS_PER_CLIENT_PER_HOUR,
    MAX_CONFIRMATIONS_PER_HOUR,
    MAX_UNCONFIRMED_PER_EMAIL,
)

pytestmark = pytest.mark.integration


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

    # GET renders a prompt and mutates nothing (mail scanners prefetch links).
    prompt = client.get(f"/watches/confirm/{token}")
    assert prompt.status_code == 200 and "Confirm watch" in prompt.text
    assert "w@example.com" in prompt.text
    with conn.connect() as c:
        assert c.execute(text("SELECT confirmed_at FROM watch")).scalar_one() is None

    page = client.post(f"/watches/confirm/{token}")
    assert page.status_code == 200 and "Watch confirmed" in page.text
    with conn.connect() as c:
        confirmed_at = c.execute(text("SELECT confirmed_at FROM watch")).scalar_one()
        assert confirmed_at is not None
        rows = watches_for(c, [pid])[pid]
        assert len(rows) == 1 and rows[0].email == "w@example.com" and rows[0].token == token

    again = client.post(f"/watches/confirm/{token}")  # idempotent
    assert again.status_code == 200
    with conn.connect() as c:
        assert c.execute(text("SELECT confirmed_at FROM watch")).scalar_one() == confirmed_at

    ask = client.get(f"/watches/unsubscribe/{token}")
    assert ask.status_code == 200 and "Unsubscribe" in ask.text
    with conn.connect() as c:
        assert c.execute(text("SELECT count(*) FROM watch")).scalar_one() == 1

    bye = client.post(f"/watches/unsubscribe/{token}")
    assert bye.status_code == 200 and "Unsubscribed" in bye.text
    with conn.connect() as c:
        assert c.execute(text("SELECT count(*) FROM watch")).scalar_one() == 0
    assert client.post(f"/watches/unsubscribe/{token}").status_code == 404
    assert client.get("/watches/confirm/nope").status_code == 404
    assert "Link not found" in client.get("/watches/confirm/nope").text


def test_one_click_unsubscribe_post(client: TestClient, conn: Engine, settings: Settings) -> None:
    """RFC 8058: mail clients POST `List-Unsubscribe=One-Click` to the List-Unsubscribe URL."""
    pid = _product_ids(client)[0]
    assert (
        client.post("/v1/watches", json={"product_id": pid, "email": "w@example.com"}).status_code
        == 202
    )
    token = json.loads(_outbox(settings)[0].read_text())["token"]
    r = client.post(
        f"/watches/unsubscribe/{token}",
        content=b"List-Unsubscribe=One-Click",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200 and "Unsubscribed" in r.text
    with conn.connect() as c:
        assert c.execute(text("SELECT count(*) FROM watch")).scalar_one() == 0


def test_unconfirmed_watches_are_rate_limited_per_email(
    client: TestClient, api_headers: dict[str, str]
) -> None:
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
    listed = client.get(
        "/v1/watches", params={"email": "spam@example.com"}, headers=api_headers
    ).json()
    assert len(listed) == MAX_UNCONFIRMED_PER_EMAIL


def test_confirmation_mail_has_a_global_hourly_budget(client: TestClient, conn: Engine) -> None:
    """Per-email quotas do not bound mail to invented addresses; the hourly cap does. Rows older
    than an hour no longer count."""
    pid = _product_ids(client)[0]
    with conn.begin() as c:
        c.execute(
            text(
                "INSERT INTO watch (product_id, email, min_discount_pct, token, "
                "confirmation_sent_at) SELECT :pid, 'bot' || g || '@example.com', 10, 'tok' || g, "
                "now() - INTERVAL '10 minutes' FROM generate_series(1, :n) g"
            ),
            {"pid": pid, "n": MAX_CONFIRMATIONS_PER_HOUR},
        )
    r = client.post("/v1/watches", json={"product_id": pid, "email": "fresh@example.com"})
    assert r.status_code == 429 and "try again later" in r.json()["detail"]
    with conn.begin() as c:
        c.execute(text("UPDATE watch SET confirmation_sent_at = now() - INTERVAL '2 hours'"))
    r = client.post("/v1/watches", json={"product_id": pid, "email": "fresh@example.com"})
    assert r.status_code == 202


def test_pending_duplicate_requeues_confirmation_and_confirmed_is_409(
    client: TestClient, conn: Engine, settings: Settings
) -> None:
    """A request for a pending (product, email) re-queues the confirmation once the previous
    one is 10 minutes old — this heals a crash between commit and outbox publish — and keeps
    the same token; a confirmed duplicate is a 409."""
    pid = _product_ids(client)[0]
    body = {"product_id": pid, "email": "w@example.com"}
    assert client.post("/v1/watches", json=body).status_code == 202
    assert client.post("/v1/watches", json=body).status_code == 202  # too soon: no new mail
    assert len(_outbox(settings)) == 1
    with conn.begin() as c:
        c.execute(text("UPDATE watch SET confirmation_sent_at = now() - INTERVAL '11 minutes'"))
    assert client.post("/v1/watches", json=body).status_code == 202
    files = _outbox(settings)
    assert len(files) == 2
    tokens = {json.loads(f.read_text())["token"] for f in files}
    assert len(tokens) == 1
    with conn.connect() as c:
        row = c.execute(text("SELECT count(*), max(confirmation_sent_at) FROM watch")).one()
        assert row[0] == 1 and row[1] is not None
    client.post(f"/watches/confirm/{tokens.pop()}")
    dup = client.post("/v1/watches", json=body)
    assert dup.status_code == 409 and "check your inbox" in dup.json()["detail"]


def test_confirmation_mail_has_a_per_client_budget(client: TestClient, conn: Engine) -> None:
    """One client (first X-Forwarded-For hop) gets MAX_CONFIRMATIONS_PER_CLIENT_PER_HOUR
    confirmation mails an hour, whatever addresses it invents; another client is unaffected.
    The hash leaves the row on confirmation."""
    ids = _product_ids(client)
    bot = {"X-Forwarded-For": "203.0.113.9, 130.176.0.1"}
    for i in range(MAX_CONFIRMATIONS_PER_CLIENT_PER_HOUR):
        r = client.post(
            "/v1/watches", json={"product_id": ids[0], "email": f"v{i}@example.com"}, headers=bot
        )
        assert r.status_code == 202
    r = client.post(
        "/v1/watches", json={"product_id": ids[0], "email": "v99@example.com"}, headers=bot
    )
    assert r.status_code == 429 and "try again later" in r.json()["detail"]
    other = {"X-Forwarded-For": "198.51.100.7"}
    r = client.post(
        "/v1/watches", json={"product_id": ids[0], "email": "v99@example.com"}, headers=other
    )
    assert r.status_code == 202
    with conn.connect() as c:
        hashes = c.execute(text("SELECT count(DISTINCT client_hash) FROM watch")).scalar_one()
        assert hashes == 2
        token = c.execute(
            text("SELECT token FROM watch WHERE email = 'v99@example.com'")
        ).scalar_one()
    client.post(f"/watches/confirm/{token}")
    with conn.connect() as c:
        assert (
            c.execute(
                text("SELECT client_hash FROM watch WHERE email = 'v99@example.com'")
            ).scalar_one()
            is None
        )


def test_unsubscribe_all_removes_every_watch_of_the_address(
    client: TestClient, conn: Engine, settings: Settings
) -> None:
    """The List-Unsubscribe target: one-click POST on any of the address's tokens deletes all
    of its watches; another address is untouched; GET only renders the prompt."""
    ids = _product_ids(client)
    for pid in ids[:3]:
        assert (
            client.post(
                "/v1/watches", json={"product_id": pid, "email": "w@example.com"}
            ).status_code
            == 202
        )
    assert (
        client.post(
            "/v1/watches", json={"product_id": ids[0], "email": "o@example.com"}
        ).status_code
        == 202
    )
    messages = [json.loads(f.read_text()) for f in _outbox(settings)]
    token = next(m["token"] for m in messages if m["email"] == "w@example.com")
    ask = client.get(f"/watches/unsubscribe-all/{token}")
    assert ask.status_code == 200 and "3 watched products" in ask.text
    with conn.connect() as c:
        assert c.execute(text("SELECT count(*) FROM watch")).scalar_one() == 4
    r = client.post(
        f"/watches/unsubscribe-all/{token}",
        content=b"List-Unsubscribe=One-Click",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200 and "3 watches removed" in r.text
    with conn.connect() as c:
        emails = c.execute(text("SELECT DISTINCT email FROM watch")).scalars().all()
        assert emails == ["o@example.com"]
    assert client.post(f"/watches/unsubscribe-all/{token}").status_code == 404
