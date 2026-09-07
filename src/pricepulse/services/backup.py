"""Watch backup: the one table raw payloads cannot rebuild (emails, tokens, confirmations).

A daily dump goes to the raw bucket as backup/watch/<date>/<time>.json.gz; restore re-keys rows
on (source code, external id, email) so it survives a product-id reset (fresh database, replayed
ingestion). Rows whose product no longer exists are skipped, existing (product, email) pairs are
left untouched: a restore never overwrites a live subscription.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, text

from pricepulse.config import Settings
from pricepulse.storage.raw import make_raw_store

_DUMP = text(
    """
    SELECT s.code AS source, p.external_id, w.email, w.min_discount_pct, w.token,
           w.created_at, w.confirmed_at, w.confirmation_sent_at
      FROM watch w
      JOIN product p ON p.id = w.product_id
      JOIN source s ON s.id = p.source_id
     ORDER BY w.id
    """
)

# Payload rows joined to the product they name; product_id is NULL when it is gone.
_ROWS = """
    SELECT u.email, u.min_discount_pct, u.token, u.created_at, u.confirmed_at,
           u.confirmation_sent_at, p.id AS product_id
      FROM unnest(CAST(:sources AS text[]), CAST(:external_ids AS text[]),
                  CAST(:emails AS text[]), CAST(:min_discount_pcts AS numeric[]),
                  CAST(:tokens AS text[]), CAST(:created_ats AS timestamptz[]),
                  CAST(:confirmed_ats AS timestamptz[]),
                  CAST(:confirmation_sent_ats AS timestamptz[]))
           AS u(source, external_id, email, min_discount_pct, token, created_at,
                confirmed_at, confirmation_sent_at)
      LEFT JOIN source s ON s.code = u.source
      LEFT JOIN product p ON p.source_id = s.id AND p.external_id = u.external_id
"""

_RESTORE = text(
    f"""
    WITH wanted AS ({_ROWS}),
    ins AS (
      INSERT INTO watch (product_id, email, min_discount_pct, token, created_at,
                         confirmed_at, confirmation_sent_at)
      SELECT product_id, email, min_discount_pct, token, created_at,
             confirmed_at, confirmation_sent_at
        FROM wanted WHERE product_id IS NOT NULL
      ON CONFLICT (product_id, email) DO NOTHING
      RETURNING 1
    )
    SELECT (SELECT count(*) FROM ins) AS restored,
           (SELECT count(*) FROM wanted WHERE product_id IS NULL) AS missing,
           (SELECT count(*) FROM wanted WHERE product_id IS NOT NULL) AS matched
    """  # noqa: S608 - constant fragment
)

_DRY_RUN = text(
    f"""
    WITH wanted AS ({_ROWS})
    SELECT count(*) FILTER (WHERE wanted.product_id IS NOT NULL AND w.id IS NULL) AS restored,
           count(*) FILTER (WHERE wanted.product_id IS NULL) AS missing,
           count(*) FILTER (WHERE w.id IS NOT NULL) AS existing
      FROM wanted
      LEFT JOIN watch w ON w.product_id = wanted.product_id AND w.email = wanted.email
    """  # noqa: S608 - constant fragment
)


@dataclass(slots=True)
class RestoreResult:
    restored: int
    skipped_missing_product: int
    skipped_existing: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def dump_watches(conn: Connection) -> dict[str, Any]:
    watches = [
        {
            "source": r.source,
            "external_id": r.external_id,
            "email": r.email,
            "min_discount_pct": str(r.min_discount_pct),
            "token": r.token,
            "created_at": _iso(r.created_at),
            "confirmed_at": _iso(r.confirmed_at),
            "confirmation_sent_at": _iso(r.confirmation_sent_at),
        }
        for r in conn.execute(_DUMP)
    ]
    return {"dumped_at": datetime.now(UTC).isoformat(), "watches": watches}


def backup_key(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"backup/watch/{now:%Y-%m-%d}/{now:%H%M%S}.json.gz"


def backup_watches(settings: Settings, engine: Engine) -> tuple[str, int]:
    """Dump every watch to the raw store; returns (key, number of watches)."""
    with engine.begin() as conn:
        payload = dump_watches(conn)
    key = make_raw_store(settings).put(backup_key(), payload)
    return key, len(payload["watches"])


def restore_watches(
    conn: Connection, payload: dict[str, Any], dry_run: bool = False
) -> RestoreResult:
    watches = payload["watches"]
    params = {
        "sources": [w["source"] for w in watches],
        "external_ids": [w["external_id"] for w in watches],
        "emails": [w["email"] for w in watches],
        "min_discount_pcts": [w["min_discount_pct"] for w in watches],
        "tokens": [w["token"] for w in watches],
        "created_ats": [w["created_at"] for w in watches],
        "confirmed_ats": [w["confirmed_at"] for w in watches],
        "confirmation_sent_ats": [w["confirmation_sent_at"] for w in watches],
    }
    if dry_run:
        row = conn.execute(_DRY_RUN, params).one()
        return RestoreResult(int(row.restored), int(row.missing), int(row.existing))
    row = conn.execute(_RESTORE, params).one()
    return RestoreResult(int(row.restored), int(row.missing), int(row.matched - row.restored))
