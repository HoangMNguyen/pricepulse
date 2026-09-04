"""API contract tests against a seeded Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricepulse.api.app import create_app
from pricepulse.config import Settings

pytestmark = pytest.mark.integration
HEADERS = {"X-API-Key": "test-key"}


def seed(engine: Engine, n: int = 12) -> None:
    """n IKEA products with discounts n*2%, n*2-2%, ... plus one Uniqlo flagged product."""
    with engine.begin() as c:
        run_id = c.execute(
            text(
                "INSERT INTO ingestion_run (source_id, raw_object_key, status, finished_at) "
                "VALUES (1, 'raw/seed', 'succeeded', now()) RETURNING id"
            )
        ).scalar()
        c.execute(text("SELECT ensure_price_partition(now())"))
        for i in range(1, n + 1):
            pid = c.execute(
                text(
                    "INSERT INTO product (source_id, external_id, name, url) "
                    "VALUES (1, :ext, :name, 'https://ikea.example/' || :ext) RETURNING id"
                ),
                {"ext": f"ikea-{i}", "name": f"KALLAX shelf {i}"},
            ).scalar()
            c.execute(
                text(
                    "INSERT INTO price_observation (product_id, observed_at, run_id, price, "
                    "list_price, retailer_sale_flag, retailer_tag) "
                    "VALUES (:pid, now(), :run, :price, 100, true, 'FAMILY_PRICE')"
                ),
                {"pid": pid, "run": run_id, "price": Decimal(100 - 2 * i)},
            )
        pid = c.execute(
            text(
                "INSERT INTO product (source_id, external_id, name, url) "
                "VALUES (2, 'E1-000', 'AIRism tee', 'https://uniqlo.example/E1') RETURNING id"
            )
        ).scalar()
        c.execute(
            text(
                "INSERT INTO price_observation (product_id, observed_at, run_id, price, "
                "retailer_sale_flag, retailer_tag) "
                "VALUES (:pid, now(), :run, 19.9, true, 'discount')"
            ),
            {"pid": pid, "run": run_id},
        )
        c.execute(text("REFRESH MATERIALIZED VIEW product_price_summary"))


@pytest.fixture
def client(
    conn: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    seed(conn)
    monkeypatch.setattr("pricepulse.api.deps.get_engine", lambda: conn)
    monkeypatch.setattr("pricepulse.api.deps.get_settings", lambda: settings)
    with TestClient(create_app()) as c:
        yield c


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["db"] == "ok"


def test_deals_ordered_and_keyset_paginated(client: TestClient) -> None:
    page1 = client.get("/v1/deals", params={"limit": 5}).json()
    assert len(page1["items"]) == 5 and page1["next_cursor"]
    pcts = [Decimal(i["discount_pct"]) for i in page1["items"]]
    assert pcts == sorted(pcts, reverse=True)
    assert pcts[0] == Decimal("24.0")  # product 12: 76 vs 100
    page2 = client.get("/v1/deals", params={"limit": 5, "cursor": page1["next_cursor"]}).json()
    ids1 = {i["product_id"] for i in page1["items"]}
    ids2 = {i["product_id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert Decimal(page2["items"][0]["discount_pct"]) <= pcts[-1]
    # walk to the end
    seen, cursor = set(), None
    while True:
        page = client.get("/v1/deals", params={"limit": 4, "cursor": cursor}).json()
        seen.update(i["product_id"] for i in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 13


def test_deals_filters(client: TestClient) -> None:
    body = client.get("/v1/deals", params={"min_discount": 20}).json()
    assert {i["name"] for i in body["items"]} == {f"KALLAX shelf {i}" for i in (10, 11, 12)}
    uniqlo = client.get("/v1/deals", params={"source": "uniqlo"}).json()["items"]
    assert len(uniqlo) == 1
    assert uniqlo[0]["discount_pct"] == "0.0" and uniqlo[0]["is_on_sale"] is True
    assert client.get("/v1/deals", params={"q": "AIRism"}).json()["items"][0]["source"] == "uniqlo"
    assert client.get("/v1/deals", params={"source": "nope"}).status_code == 400
    assert client.get("/v1/deals", params={"cursor": "!!!"}).status_code == 400


def test_product_and_history(client: TestClient) -> None:
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    product = client.get(f"/v1/products/{pid}").json()
    assert product["list_price"] == "100.00" and product["observations_90d"] == 1
    history = client.get(f"/v1/products/{pid}/history", params={"days": 30}).json()
    assert len(history) == 1 and history[0]["price"] == "76.00"
    assert client.get("/v1/products/999999").status_code == 404
    assert client.get("/v1/products/999999/history").status_code == 404


def test_runs_and_stats(client: TestClient) -> None:
    runs = client.get("/v1/runs").json()
    assert runs[0]["status"] == "succeeded" and runs[0]["source"] == "ikea"
    stats = {s["source"]: s for s in client.get("/v1/stats").json()}
    assert stats["ikea"]["products"] == 12 and stats["ikea"]["on_sale"] == 12
    assert stats["uniqlo"]["on_sale"] == 1


def test_watch_flow_requires_api_key(client: TestClient) -> None:
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    body = {"product_id": pid, "email": "w@example.com", "min_discount_pct": 5}
    assert client.post("/v1/watches", json=body).status_code == 401
    assert client.post("/v1/watches", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    created = client.post("/v1/watches", json=body, headers=HEADERS)
    assert created.status_code == 201 and created.json()["email"] == "w@example.com"
    assert client.post("/v1/watches", json=body, headers=HEADERS).status_code == 409
    assert (
        client.post("/v1/watches", json={**body, "product_id": 999999}, headers=HEADERS).status_code
        == 404
    )
    listed = client.get("/v1/watches", params={"email": "w@example.com"}, headers=HEADERS).json()
    assert [w["id"] for w in listed] == [created.json()["id"]]
    assert client.delete(f"/v1/watches/{created.json()['id']}", headers=HEADERS).status_code == 204
    assert client.delete(f"/v1/watches/{created.json()['id']}", headers=HEADERS).status_code == 404


def test_dashboard_renders(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200 and "Current deals" in home.text
    partial = client.get("/partials/deals", params={"min_discount": 20})
    assert partial.status_code == 200 and partial.text.count("<tr>") == 3
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    page = client.get(f"/products/{pid}")
    assert page.status_code == 200 and "KALLAX shelf 12" in page.text
    assert client.get("/products/999999").status_code == 404
