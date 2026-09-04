"""SQL for ingestion and the API. Kept as explicit statements so the schema tricks
(partitions, ON CONFLICT upserts, DISTINCT ON, keyset pagination) are visible in one place."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from pricepulse.domain.models import ProductSnapshot

CHUNK = 500

SOURCE_IDS = {"ikea": 1, "uniqlo": 2}


@dataclass(frozen=True, slots=True)
class PrevObservation:
    price: Decimal
    retailer_sale_flag: bool


@dataclass(frozen=True, slots=True)
class WatchRow:
    product_id: int
    email: str
    min_discount_pct: Decimal


def claim_run(conn: Connection, source_id: int, raw_object_key: str) -> int | None:
    """Idempotency gate. Returns the run id to process, or None when this key was already
    processed (succeeded) or is being processed by a live worker (running < 30 min)."""
    row = conn.execute(
        text(
            """
            INSERT INTO ingestion_run (source_id, raw_object_key, status)
            VALUES (:source_id, :key, 'running')
            ON CONFLICT (raw_object_key) DO UPDATE
              SET status = 'running', started_at = now(), error = NULL, finished_at = NULL
              WHERE ingestion_run.status = 'failed'
                 OR (ingestion_run.status = 'running'
                     AND ingestion_run.started_at < now() - INTERVAL '30 minutes')
            RETURNING id
            """
        ),
        {"source_id": source_id, "key": raw_object_key},
    ).first()
    return None if row is None else int(row[0])


def ensure_partition(conn: Connection, observed_at: datetime) -> None:
    conn.execute(text("SELECT ensure_price_partition(:ts)"), {"ts": observed_at})


def upsert_products(
    conn: Connection, source_id: int, snapshots: Sequence[ProductSnapshot]
) -> dict[str, int]:
    """Upsert products in chunks; returns external_id -> product id."""
    ids: dict[str, int] = {}
    stmt = text(
        """
        INSERT INTO product (source_id, external_id, name, category, url, image_url, currency)
        VALUES (:source_id, :external_id, :name, :category, :url, :image_url, :currency)
        ON CONFLICT (source_id, external_id) DO UPDATE
          SET name = EXCLUDED.name, category = EXCLUDED.category, url = EXCLUDED.url,
              image_url = EXCLUDED.image_url, last_seen_at = now()
        RETURNING id, external_id
        """
    )
    for start in range(0, len(snapshots), CHUNK):
        for snap in snapshots[start : start + CHUNK]:
            row = conn.execute(
                stmt,
                {
                    "source_id": source_id,
                    "external_id": snap.external_id,
                    "name": snap.name,
                    "category": snap.category,
                    "url": snap.url,
                    "image_url": snap.image_url,
                    "currency": snap.currency,
                },
            ).one()
            ids[row.external_id] = int(row.id)
    return ids


def latest_observations(conn: Connection, product_ids: Iterable[int]) -> dict[int, PrevObservation]:
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT ON (product_id) product_id, price, retailer_sale_flag
            FROM price_observation
            WHERE product_id = ANY(:ids)
            ORDER BY product_id, observed_at DESC
            """
        ),
        {"ids": list(product_ids)},
    )
    return {int(r.product_id): PrevObservation(r.price, r.retailer_sale_flag) for r in rows}


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


def watches_for(conn: Connection, product_ids: Iterable[int]) -> dict[int, list[WatchRow]]:
    rows = conn.execute(
        text("SELECT product_id, email, min_discount_pct FROM watch WHERE product_id = ANY(:ids)"),
        {"ids": list(product_ids)},
    )
    out: dict[int, list[WatchRow]] = {}
    for r in rows:
        out.setdefault(int(r.product_id), []).append(
            WatchRow(int(r.product_id), r.email, r.min_discount_pct)
        )
    return out


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
