"""Scrape (network -> raw store) and process (raw store -> Postgres + alerts).

`run_process` is idempotent on the raw object key: re-delivering the same S3 event is a no-op.
Alert classification happens here, in Python, against the previous observation per product.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine

from pricepulse.config import Settings, get_settings
from pricepulse.db import repo
from pricepulse.db.engine import get_engine
from pricepulse.domain.models import ProductSnapshot
from pricepulse.domain.pricing import discount_pct
from pricepulse.sources import get_source
from pricepulse.sources.http import make_client
from pricepulse.storage.raw import make_key, make_raw_store

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AlertOut:
    kind: str
    product_id: int
    name: str
    url: str
    source: str
    old_price: Decimal | None
    new_price: Decimal
    discount_pct: Decimal
    emails: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProcessResult:
    run_id: int | None
    source: str
    raw_object_key: str
    products_seen: int
    observations_inserted: int
    alerts: list[AlertOut]
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (Decimals as strings) — this is the Lambda return value."""
        data = asdict(self)
        for alert in data["alerts"]:
            for key in ("old_price", "new_price", "discount_pct"):
                if alert[key] is not None:
                    alert[key] = str(alert[key])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessResult:
        alerts = [
            AlertOut(
                kind=a["kind"],
                product_id=a["product_id"],
                name=a["name"],
                url=a["url"],
                source=a["source"],
                old_price=Decimal(a["old_price"]) if a.get("old_price") is not None else None,
                new_price=Decimal(a["new_price"]),
                discount_pct=Decimal(a["discount_pct"]),
                emails=list(a.get("emails", [])),
            )
            for a in data.get("alerts", [])
        ]
        return cls(
            run_id=data.get("run_id"),
            source=data["source"],
            raw_object_key=data["raw_object_key"],
            products_seen=data.get("products_seen", 0),
            observations_inserted=data.get("observations_inserted", 0),
            alerts=alerts,
            skipped=bool(data.get("skipped", False)),
        )


def run_scrape(source_code: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    source = get_source(source_code)
    with make_client(settings) as client:
        raw = source.fetch(client)
    key = make_key(source_code)
    make_raw_store(settings).put(key, raw)
    log.info("scraped %s: %d requests -> %s", source_code, len(raw["requests"]), key)
    return key


def classify_alerts(
    snapshots: list[tuple[int, ProductSnapshot]],
    prev: dict[int, repo.PrevObservation],
    watches: dict[int, list[repo.WatchRow]],
    threshold: Decimal,
) -> list[AlertOut]:
    alerts: list[AlertOut] = []
    for pid, snap in snapshots:
        before = prev.get(pid)
        common = {
            "product_id": pid,
            "name": snap.name,
            "url": snap.url,
            "source": snap.source,
            "new_price": snap.price,
        }
        if before is None:
            pct = discount_pct(snap.price, snap.list_price)
            if snap.list_price is not None and pct >= threshold:
                alerts.append(
                    AlertOut(kind="new_deal", old_price=snap.list_price, discount_pct=pct, **common)
                )
            continue
        reference = snap.list_price or before.price
        dropped = snap.price < before.price
        pct = discount_pct(snap.price, reference) if dropped else Decimal("0.0")
        if dropped and pct >= threshold:
            alerts.append(
                AlertOut(kind="price_drop", old_price=before.price, discount_pct=pct, **common)
            )
        if not before.retailer_sale_flag and snap.retailer_sale_flag:
            alerts.append(
                AlertOut(
                    kind="retailer_flag",
                    old_price=before.price,
                    discount_pct=discount_pct(snap.price, reference),
                    **common,
                )
            )
        if dropped:
            emails = [w.email for w in watches.get(pid, []) if pct >= w.min_discount_pct]
            if emails:
                alerts.append(
                    AlertOut(
                        kind="watch_hit",
                        old_price=before.price,
                        discount_pct=pct,
                        emails=emails,
                        **common,
                    )
                )
    return alerts


def run_process(
    key: str, settings: Settings | None = None, engine: Engine | None = None
) -> ProcessResult:
    settings = settings or get_settings()
    engine = engine or get_engine()
    raw = make_raw_store(settings).get(key)
    source_code = raw["source"]
    source = get_source(source_code)
    source_id = repo.SOURCE_IDS[source_code]
    observed_at = datetime.fromisoformat(raw["fetched_at"])

    with engine.begin() as conn:
        run_id = repo.claim_run(conn, source_id, key)
    if run_id is None:
        log.info("skip %s: already processed", key)
        return ProcessResult(None, source_code, key, 0, 0, [], skipped=True)

    try:
        snapshots = source.parse(raw)
        with engine.begin() as conn:
            repo.ensure_partition(conn, observed_at)
            ids = repo.upsert_products(conn, source_id, snapshots)
            rows = [(ids[s.external_id], s) for s in snapshots]
            prev = repo.latest_observations(conn, ids.values())
            inserted = repo.insert_observations(conn, run_id, observed_at, rows)
            watches = repo.watches_for(conn, ids.values())
            alerts = classify_alerts(rows, prev, watches, settings.alert_min_discount_pct)
            repo.insert_alerts(conn, run_id, [asdict(a) for a in alerts])
            repo.finish_run(conn, run_id, len(snapshots), inserted)
    except Exception as exc:
        with engine.begin() as conn:
            repo.fail_run(conn, run_id, f"{type(exc).__name__}: {exc}")
        raise

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        repo.refresh_summary(conn)
        dropped = repo.prune_partitions(conn, settings.retention_months)

    log.info(
        "processed %s: products=%d inserted=%d alerts=%d partitions_dropped=%d",
        key,
        len(snapshots),
        inserted,
        len(alerts),
        dropped,
    )
    return ProcessResult(run_id, source_code, key, len(snapshots), inserted, alerts)
