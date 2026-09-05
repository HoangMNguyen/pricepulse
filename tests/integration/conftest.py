"""Seeded API client shared by the integration modules."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricepulse.api.app import create_app
from pricepulse.config import Settings

SEED_VARIANTS = {
    "colours": [
        {
            "code": "09",
            "name": "BLACK",
            "image": "https://img.example/09.jpg",
            "chip": None,
            "sizes": ["S", "M"],
        },
        {
            "code": "64",
            "name": "BLUE",
            "image": None,
            "chip": "https://img.example/64_chip.jpg",
            "sizes": ["M"],
        },
    ],
    "sizes": [
        {"code": "003", "name": "S", "in_stock": True},
        {"code": "004", "name": "M", "in_stock": True},
        {"code": "005", "name": "L", "in_stock": False},
    ],
    "colour_total": 5,
    "stock_at": "2026-09-04T13:10:00+00:00",
}
SEED_LABELS = ["last_chance", "select_variants"]


def seed(engine: Engine, n: int = 12) -> None:
    """n IKEA products with discounts n*2%, n*2-2%, ... plus one Uniqlo flagged product and one
    IKEA product that is not on sale. first_seen_at is spread over 3 days (i % 3 days ago);
    product n has valid_to in 2 days; product n-1 has an earlier, higher observation. Each
    source gets its own succeeded run so every seeded product is current."""
    with engine.begin() as c:
        run_id = c.execute(
            text(
                "INSERT INTO ingestion_run (source_id, raw_object_key, status, finished_at) "
                "VALUES (1, 'raw/seed', 'succeeded', now()) RETURNING id"
            )
        ).scalar()
        uniqlo_run_id = c.execute(
            text(
                "INSERT INTO ingestion_run (source_id, raw_object_key, status, finished_at) "
                "VALUES (2, 'raw/seed-uniqlo', 'succeeded', now()) RETURNING id"
            )
        ).scalar()
        c.execute(text("SELECT ensure_price_partition(now())"))
        for i in range(1, n + 1):
            pid = c.execute(
                text(
                    "INSERT INTO product (source_id, external_id, name, category, url, "
                    "first_seen_at) VALUES (1, :ext, :name, :cat, 'https://ikea.example/' || :ext, "
                    "now() - make_interval(days => :age)) RETURNING id"
                ),
                {
                    "ext": f"ikea-{i}",
                    "name": f"KALLAX shelf {i}",
                    "cat": "Storage & organization" if i % 2 == 0 else "Baby & kids",
                    "age": i % 3,
                },
            ).scalar()
            if i == n - 1:
                c.execute(
                    text(
                        "INSERT INTO price_observation (product_id, observed_at, run_id, price, "
                        "list_price, retailer_sale_flag, retailer_tag) "
                        "VALUES (:pid, now() - INTERVAL '1 hour', :run, 90, 100, true, "
                        "'FAMILY_PRICE')"
                    ),
                    {"pid": pid, "run": run_id},
                )
            c.execute(
                text(
                    "INSERT INTO price_observation (product_id, observed_at, run_id, price, "
                    "list_price, retailer_sale_flag, retailer_tag, valid_to) "
                    "VALUES (:pid, now(), :run, :price, 100, true, 'FAMILY_PRICE', :valid_to)"
                ),
                {
                    "pid": pid,
                    "run": run_id,
                    "price": Decimal(100 - 2 * i),
                    "valid_to": datetime.now(UTC).date() + timedelta(days=2) if i == n else None,
                },
            )
        pid = c.execute(
            text(
                "INSERT INTO product (source_id, external_id, name, category, url) VALUES "
                "(1, 'ikea-full', 'BILLY bookcase', 'Storage & organization', "
                "'https://ikea.example/billy') RETURNING id"
            )
        ).scalar()
        c.execute(
            text(
                "INSERT INTO price_observation (product_id, observed_at, run_id, price, "
                "retailer_sale_flag) VALUES (:pid, now(), :run, 59, false)"
            ),
            {"pid": pid, "run": run_id},
        )
        pid = c.execute(
            text(
                "INSERT INTO product (source_id, external_id, name, category, url, variants, "
                "labels) VALUES (2, 'E1-000', 'AIRism tee', 'MEN', 'https://uniqlo.example/E1', "
                "CAST(:variants AS jsonb), CAST(:labels AS jsonb)) RETURNING id"
            ),
            {"variants": json.dumps(SEED_VARIANTS), "labels": json.dumps(SEED_LABELS)},
        ).scalar()
        c.execute(
            text(
                "INSERT INTO price_observation (product_id, observed_at, run_id, price, "
                "retailer_sale_flag, retailer_tag) "
                "VALUES (:pid, now(), :run, 19.9, true, 'discount')"
            ),
            {"pid": pid, "run": uniqlo_run_id},
        )
        c.execute(text("REFRESH MATERIALIZED VIEW product_price_summary"))


@pytest.fixture
def api_headers() -> dict[str, str]:
    return {"X-API-Key": "test-key"}


@pytest.fixture
def seeded_variants() -> dict:
    return {"variants": SEED_VARIANTS, "labels": SEED_LABELS}


@pytest.fixture
def client(
    conn: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    seed(conn)
    monkeypatch.setattr("pricepulse.api.deps.get_engine", lambda: conn)
    monkeypatch.setattr("pricepulse.api.deps.get_settings", lambda: settings)
    with TestClient(create_app()) as c:
        yield c
