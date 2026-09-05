"""End-to-end ingestion against a real Postgres: idempotency, alerts, summary refresh."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from pricepulse.config import Settings
from pricepulse.db import repo
from pricepulse.services.ingest import run_process
from pricepulse.storage.raw import LocalRawStore

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def ikea_raw(fetched_at: str = "2026-09-04T13:00:00+00:00") -> dict:
    return {
        "source": "ikea",
        "fetched_at": fetched_at,
        "requests": [
            {
                "url": "offers",
                "status": 200,
                "body": json.loads((FIXTURES / "ikea_offers.json").read_text()),
            }
        ],
    }


def synthetic_ikea(price: str, fetched_at: str, list_price: str | None = None) -> dict:
    """A one-product IKEA payload with controllable price/list price."""
    raw = ikea_raw(fetched_at)
    main = raw["requests"][0]["body"]["searchResultPage"]["products"]["main"]
    item = main["items"][0]
    main["items"] = [item]
    sp = item["product"]["salesPrice"]
    sp["numeral"] = float(price)
    whole, _, dec = price.partition(".")
    sp["current"] = {"wholeNumber": whole, "decimals": dec or "00"}
    if list_price:
        lw, _, ld = list_price.partition(".")
        sp["previous"] = {"wholeNumber": lw, "decimals": ld or "00"}
    else:
        sp.pop("previous", None)
    return raw


def store(settings: Settings) -> LocalRawStore:
    return LocalRawStore(settings.raw_local_dir)


def test_process_is_idempotent_per_key(conn: Engine, settings: Settings) -> None:
    key = store(settings).put("raw/ikea/2026-09-04/a.json.gz", ikea_raw())
    first = run_process(key, settings, conn)
    assert first.skipped is False
    assert first.products_seen == 5
    assert first.observations_inserted == 5
    second = run_process(key, settings, conn)
    assert second.skipped is True and second.alerts == []
    with conn.connect() as c:
        assert c.execute(text("SELECT count(*) FROM price_observation")).scalar() == 5
        assert c.execute(text("SELECT count(*) FROM ingestion_run")).scalar() == 1
        assert c.execute(text("SELECT status FROM ingestion_run")).scalar() == "succeeded"


def test_variants_and_labels_are_stored_and_overwritten(conn: Engine, settings: Settings) -> None:
    s = store(settings)
    uniqlo = {
        "source": "uniqlo",
        "fetched_at": "2026-09-04T13:10:00+00:00",
        "requests": [
            {
                "url": "p0",
                "status": 200,
                "path": "22211",
                "body": json.loads((FIXTURES / "uniqlo_men_page0.json").read_text()),
            }
        ],
    }
    run_process(s.put("raw/uniqlo/2026-09-04/u.json.gz", uniqlo), settings, conn)
    run_process(s.put("raw/ikea/2026-09-04/i.json.gz", ikea_raw()), settings, conn)
    with conn.connect() as c:
        row = c.execute(
            text("SELECT variants, labels FROM product WHERE external_id = 'E450544-000'")
        ).one()
        assert len(row.variants["colours"]) == 5 and row.variants["colour_total"] == 17
        assert row.labels == ["coming_soon"]
        assert (
            c.execute(
                text("SELECT count(*) FROM product WHERE external_id LIKE 'E450544-000/%'")
            ).scalar()
            == 2
        )
        ikea = c.execute(
            text("SELECT variants, labels FROM product WHERE external_id = '00434277'")
        ).one()
        assert ikea.variants is None and ikea.labels == ["last_chance", "in_store_only"]
    # next run: one colour left, flags gone -> the current-state columns are overwritten
    item = next(
        i
        for i in uniqlo["requests"][0]["body"]["result"]["items"]
        if (i["productId"], i["priceGroup"]) == ("E450544-000", "00")
    )
    item["colors"] = item["colors"][:1]
    item["representative"]["flags"]["productFlags"] = []
    uniqlo["fetched_at"] = "2026-09-05T13:10:00+00:00"
    run_process(s.put("raw/uniqlo/2026-09-05/u.json.gz", uniqlo), settings, conn)
    with conn.connect() as c:
        row = c.execute(
            text("SELECT variants, labels FROM product WHERE external_id = 'E450544-000'")
        ).one()
        assert len(row.variants["colours"]) == 1 and row.labels == []


def test_failed_run_can_be_retried(conn: Engine, settings: Settings) -> None:
    key = store(settings).put("raw/ikea/2026-09-04/b.json.gz", ikea_raw())
    with conn.begin() as c:
        c.execute(
            text(
                "INSERT INTO ingestion_run (source_id, raw_object_key, status, error) "
                "VALUES (1, :k, 'failed', 'boom')"
            ),
            {"k": key},
        )
    result = run_process(key, settings, conn)
    assert result.skipped is False and result.products_seen == 5
    with conn.connect() as c:
        row = c.execute(text("SELECT status, error FROM ingestion_run")).one()
        assert (row.status, row.error) == ("succeeded", None)


def test_post_processing_failure_is_retryable_with_alerts(
    conn: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the DML commit (summary refresh) marks the run failed; the retry
    re-inserts nothing and recomputes the same alerts, so the digest still goes out."""
    key = store(settings).put(
        "raw/ikea/2026-09-04/r.json.gz",
        synthetic_ikea("70.00", "2026-09-04T13:00:00+00:00", list_price="100.00"),
    )

    def raiser(_conn: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("pricepulse.db.repo.refresh_summary", raiser)
    with pytest.raises(RuntimeError):
        run_process(key, settings, conn)
    with conn.connect() as c:
        row = c.execute(text("SELECT status, error FROM ingestion_run")).one()
        assert row.status == "failed" and row.error.startswith("RuntimeError")
    monkeypatch.undo()
    result = run_process(key, settings, conn)
    assert result.skipped is False and result.observations_inserted == 0
    assert [a.kind for a in result.alerts] == ["new_deal"]
    with conn.connect() as c:
        assert c.execute(text("SELECT status FROM ingestion_run")).scalar_one() == "succeeded"
        assert c.execute(text("SELECT count(*) FROM alert")).scalar_one() == 1


def test_ensure_source_registers_new_code(conn: Engine) -> None:
    with conn.begin() as c:
        try:
            assert repo.ensure_source(c, "acme", "ACME US", "https://acme.example/") == 3
            assert repo.ensure_source(c, "acme", "ACME", "https://acme.example/") == 3
            name = c.execute(text("SELECT name FROM source WHERE code = 'acme'")).scalar_one()
            assert name == "ACME"
            assert repo.ensure_source(c, "ikea", "IKEA US", "https://www.ikea.com/us/en/") == 1
        finally:
            c.execute(text("DELETE FROM source WHERE code = 'acme'"))


def test_new_deal_alert_from_retailer_list_price(conn: Engine, settings: Settings) -> None:
    key = store(settings).put("raw/ikea/2026-09-04/c.json.gz", ikea_raw())
    result = run_process(key, settings, conn)
    # fixture: 00473546 at 79.99 vs 95.00 = 15.8% < threshold 20 -> no new_deal for it
    assert all(a.kind != "new_deal" or a.discount_pct >= Decimal("20") for a in result.alerts)
    with conn.connect() as c:
        row = c.execute(
            text(
                "SELECT s.current_price, s.list_price, s.reference_price, s.discount_pct "
                "FROM product_price_summary s JOIN product p ON p.id = s.product_id "
                "WHERE p.external_id = '00473546'"
            )
        ).one()
    assert (row.current_price, row.list_price) == (Decimal("79.99"), Decimal("95.00"))
    assert row.reference_price == Decimal("95.00")
    assert row.discount_pct == Decimal("15.8")


def test_price_drop_across_two_runs(conn: Engine, settings: Settings) -> None:
    s = store(settings)
    k1 = s.put(
        "raw/ikea/2026-09-01/d.json.gz", synthetic_ikea("100.00", "2026-09-01T13:00:00+00:00")
    )
    k2 = s.put(
        "raw/ikea/2026-09-02/e.json.gz", synthetic_ikea("70.00", "2026-09-02T13:00:00+00:00")
    )
    assert run_process(k1, settings, conn).alerts == []
    result = run_process(k2, settings, conn)
    kinds = [a.kind for a in result.alerts]
    assert kinds == ["price_drop"]
    alert = result.alerts[0]
    assert (alert.old_price, alert.new_price, alert.discount_pct) == (
        Decimal("100.00"),
        Decimal("70.00"),
        Decimal("30.0"),
    )
    with conn.connect() as c:
        row = c.execute(
            text(
                "SELECT discount_pct, reference_price, mode_price_90d, observations_90d "
                "FROM product_price_summary"
            )
        ).one()
        # baseline excludes the current row: reference = 100 -> history-derived 30.0
        assert (row.reference_price, row.mode_price_90d) == (Decimal("100.00"), Decimal("100.00"))
        assert row.discount_pct == Decimal("30.0")
        assert row.observations_90d == 2
        assert c.execute(text("SELECT count(*) FROM alert WHERE kind='price_drop'")).scalar() == 1


def test_history_derived_discount_with_established_baseline(
    conn: Engine, settings: Settings
) -> None:
    s = store(settings)
    for day in (1, 2, 3):
        run_process(
            s.put(
                f"raw/ikea/2026-09-0{day}/f.json.gz",
                synthetic_ikea("100.00", f"2026-09-0{day}T13:00:00+00:00"),
            ),
            settings,
            conn,
        )
    run_process(
        s.put(
            "raw/ikea/2026-09-04/g.json.gz", synthetic_ikea("70.00", "2026-09-04T13:00:00+00:00")
        ),
        settings,
        conn,
    )
    with conn.connect() as c:
        row = c.execute(
            text(
                "SELECT discount_pct, reference_price, observations_90d FROM product_price_summary"
            )
        ).one()
    assert row.observations_90d == 4
    assert row.reference_price == Decimal("100.00")
    assert row.discount_pct == Decimal("30.0")


def test_watch_hit_carries_watcher_email(conn: Engine, settings: Settings) -> None:
    s = store(settings)
    k1 = s.put(
        "raw/ikea/2026-09-01/h.json.gz", synthetic_ikea("100.00", "2026-09-01T13:00:00+00:00")
    )
    run_process(k1, settings, conn)
    with conn.begin() as c:
        c.execute(
            text(
                "INSERT INTO watch (product_id, email, min_discount_pct, token, confirmed_at) "
                "SELECT id, 'watcher@example.com', 5, 'tok-' || id, now() FROM product"
            )
        )
    k2 = s.put(
        "raw/ikea/2026-09-02/i.json.gz", synthetic_ikea("90.00", "2026-09-02T13:00:00+00:00")
    )
    result = run_process(k2, settings, conn)
    kinds = sorted(a.kind for a in result.alerts)
    assert kinds == ["watch_hit"]  # 10% drop is below the global 20% threshold
    assert result.alerts[0].emails == ["watcher@example.com"]
    assert result.alerts[0].watch_tokens == {"watcher@example.com": "tok-1"}
    payload = result.to_dict()
    assert payload["alerts"][0]["watch_tokens"] == {"watcher@example.com": "tok-1"}
    assert payload["alerts"][0]["new_price"] == "90.00"
    json.dumps(payload)  # Lambda return value must be JSON-serializable


def test_prune_drops_only_old_partitions(conn: Engine) -> None:
    with conn.begin() as c:
        c.execute(text("SELECT ensure_price_partition(now() - INTERVAL '20 months')"))
        c.execute(text("SELECT ensure_price_partition(now() - INTERVAL '12 months')"))
        old_name = c.execute(
            text(
                "SELECT format('price_observation_%s', "
                "to_char((now() - INTERVAL '20 months') AT TIME ZONE 'UTC', 'YYYY_MM'))"
            )
        ).scalar()
        assert c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": old_name}).scalar()
        assert c.execute(text("SELECT prune_price_partitions(13)")).scalar() == 1
        assert c.execute(text("SELECT to_regclass(:n) IS NULL"), {"n": old_name}).scalar()
        current = c.execute(
            text(
                "SELECT format('price_observation_%s', "
                "to_char(now() AT TIME ZONE 'UTC', 'YYYY_MM'))"
            )
        ).scalar()
        assert c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": current}).scalar()
        assert c.execute(text("SELECT prune_price_partitions(13)")).scalar() == 0
