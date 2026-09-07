"""Runs carry the payload's timestamp; "latest successful run" orders by it, not by id.

Processing order is not chronology: a delayed S3 event or a manual re-run of an older raw
object gets a higher run id than the newer payload processed before it. With `is_current`
keyed on the highest id, that older run made every product's latest observation (which belongs
to the newer run) non-current and emptied the deals list. Summary v4 picks the latest
successful run by `observed_at` (payload fetch time), so replaying history is harmless.

Revision ID: 0007_run_observed_at
Revises: 0006_variants_labels
"""

from alembic import op

revision = "0007_run_observed_at"
down_revision = "0006_variants_labels"
branch_labels = None
depends_on = None


def _summary(last_run_order: str) -> str:
    """The 0005 view body verbatim; only the `last_run` ordering differs between v3 and v4."""
    return f"""
CREATE MATERIALIZED VIEW product_price_summary AS
WITH last_run AS (
  -- The latest successful run per source; a product is "current" only when its latest
  -- observation came from that run (IKEA's feed is offers-only: items that leave it must
  -- leave the deals list, not linger with their last sale price).
  SELECT DISTINCT ON (source_id) source_id, id
  FROM ingestion_run WHERE status = 'succeeded'
  ORDER BY source_id, {last_run_order}
)
SELECT p.id AS product_id,
       p.source_id,
       src.code AS source,
       p.name,
       p.category,
       p.url,
       p.image_url,
       p.currency,
       p.first_seen_at,
       p.last_seen_at,
       cur.price AS current_price,
       cur.observed_at AS current_observed_at,
       cur.retailer_sale_flag,
       cur.retailer_tag,
       cur.valid_to,
       cur.list_price,
       COALESCE(cur.list_price, ref.mode_price) AS reference_price,
       ref.mode_price AS mode_price_90d,
       ref.min_price AS min_price_90d,
       ref.max_price AS max_price_90d,
       ref.n AS observations_90d,
       (CASE WHEN COALESCE(cur.list_price, ref.mode_price) > cur.price
             THEN round(100 * (1 - cur.price / COALESCE(cur.list_price, ref.mode_price)), 1)
             ELSE 0 END)::NUMERIC(5,1) AS discount_pct,
       prev.price AS previous_price,
       prev.observed_at AS previous_observed_at,
       GREATEST(COALESCE(cur.list_price, ref.mode_price) - cur.price, 0)::NUMERIC(10,2) AS savings,
       COALESCE(cur.run_id = lr.id, false) AS is_current
FROM product p
JOIN source src ON src.id = p.source_id
JOIN LATERAL (
  SELECT * FROM price_observation o
  WHERE o.product_id = p.id
  ORDER BY observed_at DESC
  LIMIT 1
) cur ON true
JOIN LATERAL (
  -- Baseline = prior observations in the last 90 days, excluding the current one, so a
  -- fresh drop is measured against what the item used to cost. mode() ties -> lowest.
  SELECT mode() WITHIN GROUP (ORDER BY price) AS mode_price,
         min(price) AS min_price,
         max(price) AS max_price,
         count(*) + 1 AS n
  FROM price_observation o
  WHERE o.product_id = p.id
    AND o.observed_at < cur.observed_at
    AND o.observed_at >= now() - INTERVAL '90 days'
) ref ON true
LEFT JOIN LATERAL (
  SELECT price, observed_at FROM price_observation o
  WHERE o.product_id = p.id AND o.observed_at < cur.observed_at
  ORDER BY observed_at DESC LIMIT 1
) prev ON true
LEFT JOIN last_run lr ON lr.source_id = p.source_id
"""


SUMMARY_V4 = _summary("observed_at DESC, id DESC")
SUMMARY_V3 = _summary("id DESC")

# Identical to 0005: the view is dropped and recreated, so its indexes and grants are too.
INDEXES = [
    "CREATE UNIQUE INDEX product_price_summary_pk ON product_price_summary (product_id)",
    """
    CREATE INDEX product_price_summary_discount_idx
      ON product_price_summary (discount_pct DESC, product_id) WHERE is_current
    """,
    """
    CREATE INDEX product_price_summary_savings_idx
      ON product_price_summary (savings DESC, product_id) WHERE is_current
    """,
    """
    CREATE INDEX product_price_summary_price_idx
      ON product_price_summary (current_price, product_id) WHERE is_current
    """,
    """
    CREATE INDEX product_price_summary_newest_idx
      ON product_price_summary (first_seen_at DESC, product_id) WHERE is_current
    """,
    """
    CREATE INDEX product_price_summary_ending_idx
      ON product_price_summary (valid_to, product_id)
      WHERE valid_to IS NOT NULL AND is_current
    """,
    """
    CREATE INDEX product_price_summary_category_idx
      ON product_price_summary (source_id, category) WHERE is_current
    """,
    """
    CREATE INDEX product_price_summary_name_trgm_idx
      ON product_price_summary USING gin (name gin_trgm_ops) WHERE is_current
    """,
    """
    CREATE INDEX product_price_summary_name_idx
      ON product_price_summary (name, product_id) WHERE is_current
    """,
    """
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        GRANT SELECT ON product_price_summary TO app_rw;
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_ro') THEN
        GRANT SELECT ON product_price_summary TO app_ro;
      END IF;
    END
    $$
    """,
]

UPGRADE = [
    "ALTER TABLE ingestion_run ADD COLUMN observed_at TIMESTAMPTZ",
    # Backfill from the run's own observations; a run that inserted nothing (empty or failed)
    # falls back to when it started, which preserves the previous id ordering for it.
    """
    UPDATE ingestion_run r
    SET observed_at = COALESCE(
      (SELECT min(o.observed_at) FROM price_observation o WHERE o.run_id = r.id), r.started_at)
    """,
    "ALTER TABLE ingestion_run ALTER COLUMN observed_at SET NOT NULL",
    "DROP MATERIALIZED VIEW product_price_summary",
    SUMMARY_V4,
    *INDEXES,
]

DOWNGRADE = [
    "DROP MATERIALIZED VIEW product_price_summary",
    SUMMARY_V3,
    *INDEXES,
    "ALTER TABLE ingestion_run DROP COLUMN observed_at",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
