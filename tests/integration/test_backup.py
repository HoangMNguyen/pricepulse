"""Watch dump/restore against a real Postgres: rows are re-keyed on (source, external id, email),
so a restore survives new product ids, skips vanished products and never touches live rows."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from pricepulse.config import Settings
from pricepulse.services.backup import backup_watches, restore_watches
from pricepulse.storage.raw import LocalRawStore

pytestmark = pytest.mark.integration

CONFIRMED_AT = "2026-09-01T10:00:00+00:00"


def seed_watches(engine: Engine) -> None:
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO product (source_id, external_id, name, url) VALUES "
                "(1, 'ikea-1', 'KALLAX', 'https://ikea.example/1'), "
                "(2, 'uq-1', 'Tee', 'https://uniqlo.example/1')"
            )
        )
        c.execute(
            text(
                "INSERT INTO watch (product_id, email, min_discount_pct, token, confirmed_at, "
                "confirmation_sent_at) "
                "SELECT id, 'a@example.com', 12.5, 'tok-' || external_id, "
                "CAST(:confirmed AS timestamptz), "
                "CAST(:confirmed AS timestamptz) - interval '1 hour' "
                "FROM product WHERE external_id = 'ikea-1' "
                "UNION ALL "
                "SELECT id, 'b@example.com', 0, 'tok-' || external_id, NULL, now() "
                "FROM product WHERE external_id = 'uq-1'"
            ),
            {"confirmed": CONFIRMED_AT},
        )


def test_restore_after_data_loss(conn: Engine, settings: Settings) -> None:
    seed_watches(conn)
    key, n = backup_watches(settings, conn)
    assert n == 2 and key.startswith("backup/watch/") and key.endswith(".json.gz")
    payload = LocalRawStore(settings.raw_local_dir).get(key)
    assert {w["source"] for w in payload["watches"]} == {"ikea", "uniqlo"}

    # Data loss: the watch table is gone and one product with it; the surviving product gets a
    # new id (fresh identity), so the restore must not rely on the dumped product ids.
    with conn.begin() as c:
        c.execute(text("TRUNCATE watch, product RESTART IDENTITY CASCADE"))
        c.execute(
            text(
                "INSERT INTO product (source_id, external_id, name, url) "
                "VALUES (1, 'ikea-1', 'KALLAX', 'https://ikea.example/1')"
            )
        )

    with conn.begin() as c:
        dry = restore_watches(c, payload, dry_run=True)
        assert (dry.restored, dry.skipped_missing_product, dry.skipped_existing) == (1, 1, 0)
        assert c.execute(text("SELECT count(*) FROM watch")).scalar_one() == 0

    with conn.begin() as c:
        result = restore_watches(c, payload)
    assert (result.restored, result.skipped_missing_product, result.skipped_existing) == (1, 1, 0)
    with conn.begin() as c:
        row = c.execute(
            text(
                "SELECT p.external_id, w.email, w.min_discount_pct, w.token, w.confirmed_at, "
                "w.confirmation_sent_at FROM watch w JOIN product p ON p.id = w.product_id"
            )
        ).one()
    assert (row.external_id, row.email, row.token) == ("ikea-1", "a@example.com", "tok-ikea-1")
    assert row.min_discount_pct == Decimal("12.5")
    assert row.confirmed_at == datetime.fromisoformat(CONFIRMED_AT)
    assert row.confirmation_sent_at == datetime.fromisoformat("2026-09-01T09:00:00+00:00")

    # A second restore (or a restore over a live table) leaves existing rows alone.
    with conn.begin() as c:
        c.execute(text("UPDATE watch SET min_discount_pct = 30"))
        again = restore_watches(c, payload)
        assert (again.restored, again.skipped_missing_product, again.skipped_existing) == (0, 1, 1)
        assert c.execute(text("SELECT min_discount_pct FROM watch")).scalar_one() == Decimal("30")
