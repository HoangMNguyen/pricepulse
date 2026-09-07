"""Ingestion SQL. Kept as explicit statements so the schema tricks (partitions, ON CONFLICT
upserts, DISTINCT ON, array unnest) are visible in one place. Reads for the API live in
`pricepulse.api.queries`; watch SQL in `pricepulse.db.watches`."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from pricepulse.domain.models import ProductSnapshot

CHUNK = 500
# Window for the "peak" a drop is measured against; matches `max_price_90d` in the summary.
PEAK_WINDOW_DAYS = 90


class RunInProgressError(Exception):
    """Another worker holds the claim on this raw object key and its lease has not expired."""


@dataclass(frozen=True, slots=True)
class PrevObservation:
    price: Decimal
    retailer_sale_flag: bool
    peak_price: Decimal  # highest price in the peak window ending at the previous observation


def ensure_source(conn: Connection, code: str, name: str, base_url: str) -> int:
    """Register (or refresh) the adapter's source row; returns its id. Update first: an
    INSERT ... ON CONFLICT would burn an identity value on every run."""
    params = {"code": code, "name": name, "base_url": base_url}
    row = conn.execute(
        text(
            "UPDATE source SET name = :name, base_url = :base_url WHERE code = :code RETURNING id"
        ),
        params,
    ).first()
    if row is None:
        row = conn.execute(
            text(
                """
                INSERT INTO source (code, name, base_url) VALUES (:code, :name, :base_url)
                ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, base_url = EXCLUDED.base_url
                RETURNING id
                """
            ),
            params,
        ).one()
    return int(row.id)


def claim_run(
    conn: Connection, source_id: int, raw_object_key: str, observed_at: datetime, lease_s: int
) -> int | None:
    """Idempotency gate. Returns the run id to process, or None when this key already
    succeeded. A `running` claim younger than `lease_s` belongs to a live worker: raises
    RunInProgressError so the caller fails (and Lambda's retry/failure path stays honest) instead
    of reporting a duplicate as done. `observed_at` is the payload's fetch time: chronology
    is defined by it, never by processing order."""
    row = conn.execute(
        text(
            """
            INSERT INTO ingestion_run (source_id, raw_object_key, status, observed_at)
            VALUES (:source_id, :key, 'running', :observed_at)
            ON CONFLICT (raw_object_key) DO UPDATE
              SET status = 'running', started_at = now(), error = NULL, finished_at = NULL,
                  observed_at = EXCLUDED.observed_at
              WHERE ingestion_run.status = 'failed'
                 OR (ingestion_run.status = 'running'
                     AND ingestion_run.started_at < now() - make_interval(secs => :lease_s))
            RETURNING id
            """
        ),
        {
            "source_id": source_id,
            "key": raw_object_key,
            "observed_at": observed_at,
            "lease_s": lease_s,
        },
    ).first()
    if row is not None:
        return int(row[0])
    status = conn.execute(
        text("SELECT status FROM ingestion_run WHERE raw_object_key = :key"),
        {"key": raw_object_key},
    ).scalar_one()
    if status == "running":
        raise RunInProgressError(raw_object_key)
    return None


def ensure_partition(conn: Connection, observed_at: datetime) -> None:
    conn.execute(text("SELECT ensure_price_partition(:ts)"), {"ts": observed_at})


def upsert_products(
    conn: Connection, source_id: int, snapshots: Sequence[ProductSnapshot], observed_at: datetime
) -> dict[str, int]:
    """Upsert products one statement per CHUNK (unnest of parallel arrays); returns
    external_id -> product id. `snapshots` must not repeat an external_id: duplicate keys in
    one ON CONFLICT DO UPDATE statement are an error (`parse()` de-duplicates per payload).
    The current-state columns only move forward in payload time: replaying an older payload
    (manual re-run, delayed S3 event) must not overwrite what a newer run recorded."""
    ids: dict[str, int] = {}
    stmt = text(
        """
        INSERT INTO product (source_id, external_id, name, category, url, image_url, currency,
                             variants, labels, first_seen_at, last_seen_at)
        SELECT :source_id, u.external_id, u.name, u.category, u.url, u.image_url, u.currency,
               CAST(u.variants AS jsonb), CAST(u.labels AS jsonb), :observed_at, :observed_at
        FROM unnest(CAST(:external_ids AS text[]), CAST(:names AS text[]),
                    CAST(:categories AS text[]), CAST(:urls AS text[]),
                    CAST(:image_urls AS text[]), CAST(:currencies AS text[]),
                    CAST(:variants AS text[]), CAST(:labels AS text[]))
             AS u(external_id, name, category, url, image_url, currency, variants, labels)
        ON CONFLICT (source_id, external_id) DO UPDATE
          SET name = CASE WHEN product.last_seen_at <= EXCLUDED.last_seen_at
                          THEN EXCLUDED.name ELSE product.name END,
              category = CASE WHEN product.last_seen_at <= EXCLUDED.last_seen_at
                              THEN EXCLUDED.category ELSE product.category END,
              url = CASE WHEN product.last_seen_at <= EXCLUDED.last_seen_at
                         THEN EXCLUDED.url ELSE product.url END,
              image_url = CASE WHEN product.last_seen_at <= EXCLUDED.last_seen_at
                               THEN EXCLUDED.image_url ELSE product.image_url END,
              variants = CASE WHEN product.last_seen_at <= EXCLUDED.last_seen_at
                              THEN EXCLUDED.variants ELSE product.variants END,
              labels = CASE WHEN product.last_seen_at <= EXCLUDED.last_seen_at
                            THEN EXCLUDED.labels ELSE product.labels END,
              first_seen_at = LEAST(product.first_seen_at, EXCLUDED.first_seen_at),
              last_seen_at = GREATEST(product.last_seen_at, EXCLUDED.last_seen_at)
        RETURNING id, external_id
        """
    )
    for start in range(0, len(snapshots), CHUNK):
        chunk = snapshots[start : start + CHUNK]
        rows = conn.execute(
            stmt,
            {
                "source_id": source_id,
                "observed_at": observed_at,
                "external_ids": [s.external_id for s in chunk],
                "names": [s.name for s in chunk],
                "categories": [s.category for s in chunk],
                "urls": [s.url for s in chunk],
                "image_urls": [s.image_url for s in chunk],
                "currencies": [s.currency for s in chunk],
                "variants": [None if s.variants is None else json.dumps(s.variants) for s in chunk],
                "labels": [json.dumps(list(s.labels)) for s in chunk],
            },
        )
        for row in rows:
            ids[row.external_id] = int(row.id)
    return ids


def latest_observations(
    conn: Connection, product_ids: Iterable[int], before: datetime
) -> dict[int, PrevObservation]:
    """Latest observation per product strictly older than `before` (the payload's timestamp),
    so a retried run compares against history, not its own already-inserted rows. Carries the
    peak price of the PEAK_WINDOW_DAYS before `before` (falls back to the previous price when
    that observation is older than the window)."""
    rows = conn.execute(
        text(
            """
            SELECT o.product_id, o.price, o.retailer_sale_flag,
                   COALESCE(pk.peak_price, o.price) AS peak_price
            FROM (
              SELECT DISTINCT ON (product_id) product_id, price, retailer_sale_flag
              FROM price_observation
              WHERE product_id = ANY(:ids) AND observed_at < :before
              ORDER BY product_id, observed_at DESC
            ) o
            JOIN LATERAL (
              SELECT max(price) AS peak_price
              FROM price_observation
              WHERE product_id = o.product_id
                AND observed_at < :before
                AND observed_at >= :before - make_interval(days => :window)
            ) pk ON true
            """
        ),
        {"ids": list(product_ids), "before": before, "window": PEAK_WINDOW_DAYS},
    )
    return {
        int(r.product_id): PrevObservation(r.price, r.retailer_sale_flag, r.peak_price)
        for r in rows
    }


def insert_observations(
    conn: Connection,
    run_id: int,
    observed_at: datetime,
    rows: Sequence[tuple[int, ProductSnapshot]],
) -> int:
    if not rows:
        return 0
    stmt = text(
        """
        INSERT INTO price_observation
          (product_id, observed_at, run_id, price, list_price, retailer_sale_flag, retailer_tag,
           valid_to)
        VALUES (:product_id, :observed_at, :run_id, :price, :list_price, :flag, :tag, :valid_to)
        ON CONFLICT (product_id, observed_at) DO NOTHING
        """
    )
    inserted = 0
    for start in range(0, len(rows), CHUNK):
        params = [
            {
                "product_id": pid,
                "observed_at": observed_at,
                "run_id": run_id,
                "price": s.price,
                "list_price": s.list_price,
                "flag": s.retailer_sale_flag,
                "tag": s.retailer_tag,
                "valid_to": s.valid_to,
            }
            for pid, s in rows[start : start + CHUNK]
        ]
        inserted += conn.execute(stmt, params).rowcount
    return inserted


def insert_alerts(conn: Connection, run_id: int, alerts: Sequence[dict[str, Any]]) -> None:
    if not alerts:
        return
    conn.execute(
        text(
            """
            INSERT INTO alert (run_id, product_id, kind, old_price, new_price, discount_pct)
            VALUES (:run_id, :product_id, :kind, :old_price, :new_price, :discount_pct)
            ON CONFLICT (run_id, product_id, kind) DO NOTHING
            """
        ),
        [
            {
                "run_id": run_id,
                "product_id": a["product_id"],
                "kind": a["kind"],
                "old_price": a["old_price"],
                "new_price": a["new_price"],
                "discount_pct": a["discount_pct"],
            }
            for a in alerts
        ],
    )


def finish_run(conn: Connection, run_id: int, products_seen: int, inserted: int) -> None:
    conn.execute(
        text(
            """
            UPDATE ingestion_run
            SET status = 'succeeded', finished_at = now(), products_seen = :n,
                observations_inserted = :m
            WHERE id = :id
            """
        ),
        {"id": run_id, "n": products_seen, "m": inserted},
    )


def fail_run(conn: Connection, run_id: int, error: str) -> None:
    conn.execute(
        text(
            "UPDATE ingestion_run SET status = 'failed', finished_at = now(), error = :e "
            "WHERE id = :id"
        ),
        {"id": run_id, "e": error[:4000]},
    )


def refresh_summary(conn: Connection) -> None:
    """Via the SECURITY DEFINER wrapper: the app role does not own the materialized view."""
    conn.execute(text("SELECT refresh_price_summary()"))


def prune_partitions(conn: Connection, keep_months: int) -> int:
    """Drop monthly partitions older than keep_months (SECURITY DEFINER helper). Returns count."""
    return int(conn.execute(text("SELECT prune_price_partitions(:m)"), {"m": keep_months}).scalar())
