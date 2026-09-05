"""Summary view v3: `is_current` (latest observation came from the source's latest successful
run), `source` code column, current-only indexes; guard prune_price_partitions; source ids
become an identity so adapters can self-register.

Revision ID: 0005_summary_v3
Revises: 0004_watch_optin
"""

from alembic import op

revision = "0005_summary_v3"
down_revision = "0004_watch_optin"
branch_labels = None
depends_on = None

SUMMARY_V3 = """
CREATE MATERIALIZED VIEW product_price_summary AS
WITH last_run AS (
  -- The latest successful run per source; a product is "current" only when its latest
  -- observation came from that run (IKEA's feed is offers-only: items that leave it must
  -- leave the deals list, not linger with their last sale price).
  SELECT DISTINCT ON (source_id) source_id, id
  FROM ingestion_run WHERE status = 'succeeded'
  ORDER BY source_id, id DESC
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

GRANTS = """
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
"""

UPGRADE = [
    "DROP MATERIALIZED VIEW product_price_summary",
    SUMMARY_V3,
    # Unconditional: REFRESH ... CONCURRENTLY needs a unique index covering every row.
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
    GRANTS,
    # CREATE OR REPLACE keeps the existing EXECUTE grants (0003).
    r"""
    CREATE OR REPLACE FUNCTION prune_price_partitions(keep_months INT) RETURNS INT
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
      r RECORD;
      dropped INT := 0;
      cutoff TIMESTAMPTZ := (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                            - make_interval(months => keep_months);
    BEGIN
      IF keep_months < 1 THEN
        RAISE EXCEPTION 'prune_price_partitions: keep_months must be >= 1 (got %)', keep_months;
      END IF;
      FOR r IN SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound
               FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
               WHERE i.inhparent = 'price_observation'::regclass LOOP
        -- bound: FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00')
        IF substring(r.bound FROM 'TO \(''([^'']+)''\)')::timestamptz <= cutoff THEN
          EXECUTE format('DROP TABLE %I', r.relname);
          dropped := dropped + 1;
        END IF;
      END LOOP;
      RETURN dropped;
    END
    $$
    """,
    # Adapters register their own source row (INSERT ... ON CONFLICT (code)); ids 1-2 were
    # seeded by 0001. app_rw's default privileges cover the identity sequence.
    "ALTER TABLE source ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (START WITH 3)",
]

# The 0003 definition, verbatim, for downgrade.
SUMMARY_V2 = """
CREATE MATERIALIZED VIEW product_price_summary AS
SELECT p.id AS product_id,
       p.source_id,
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
       GREATEST(COALESCE(cur.list_price, ref.mode_price) - cur.price, 0)::NUMERIC(10,2) AS savings
FROM product p
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
"""

DOWNGRADE = [
    "ALTER TABLE source ALTER COLUMN id DROP IDENTITY",
    "DROP MATERIALIZED VIEW product_price_summary",
    SUMMARY_V2,
    "CREATE UNIQUE INDEX product_price_summary_pk ON product_price_summary (product_id)",
    """
    CREATE INDEX product_price_summary_discount_idx
      ON product_price_summary (discount_pct DESC, product_id)
    """,
    """
    CREATE INDEX product_price_summary_savings_idx
      ON product_price_summary (savings DESC, product_id)
    """,
    """
    CREATE INDEX product_price_summary_price_idx
      ON product_price_summary (current_price, product_id)
    """,
    """
    CREATE INDEX product_price_summary_newest_idx
      ON product_price_summary (first_seen_at DESC, product_id)
    """,
    """
    CREATE INDEX product_price_summary_ending_idx
      ON product_price_summary (valid_to, product_id) WHERE valid_to IS NOT NULL
    """,
    """
    CREATE INDEX product_price_summary_category_idx
      ON product_price_summary (source_id, category)
    """,
    """
    CREATE INDEX product_price_summary_name_trgm_idx
      ON product_price_summary USING gin (name gin_trgm_ops)
    """,
    GRANTS,
    r"""
    CREATE OR REPLACE FUNCTION prune_price_partitions(keep_months INT) RETURNS INT
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
      r RECORD;
      dropped INT := 0;
      cutoff TIMESTAMPTZ := (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                            - make_interval(months => keep_months);
    BEGIN
      FOR r IN SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound
               FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
               WHERE i.inhparent = 'price_observation'::regclass LOOP
        -- bound: FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00')
        IF substring(r.bound FROM 'TO \(''([^'']+)''\)')::timestamptz <= cutoff THEN
          EXECUTE format('DROP TABLE %I', r.relname);
          dropped := dropped + 1;
        END IF;
      END LOOP;
      RETURN dropped;
    END
    $$
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
