"""Materialized per-product price summary used by the API and dashboard.

Revision ID: 0002_summary
Revises: 0001_schema
"""

from alembic import op

revision = "0002_summary"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE MATERIALIZED VIEW product_price_summary AS
    SELECT p.id AS product_id,
           p.source_id,
           p.name,
           p.category,
           p.url,
           p.image_url,
           p.currency,
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
                 ELSE 0 END)::NUMERIC(5,1) AS discount_pct
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
    """,
    "CREATE UNIQUE INDEX product_price_summary_pk ON product_price_summary (product_id)",
    """
    CREATE INDEX product_price_summary_deals_idx
      ON product_price_summary (discount_pct DESC, product_id)
    """,
    # REFRESH MATERIALIZED VIEW requires ownership, which stays with the migrator. The app role
    # refreshes through this SECURITY DEFINER wrapper (fixed search_path, PUBLIC revoked).
    """
    CREATE OR REPLACE FUNCTION refresh_price_summary() RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS
    'REFRESH MATERIALIZED VIEW CONCURRENTLY product_price_summary'
    """,
    "REVOKE EXECUTE ON FUNCTION refresh_price_summary() FROM PUBLIC",
    """
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        GRANT EXECUTE ON FUNCTION refresh_price_summary() TO app_rw;
        GRANT SELECT ON product_price_summary TO app_rw;
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_ro') THEN
        GRANT SELECT ON product_price_summary TO app_ro;
      END IF;
    END
    $$
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS refresh_price_summary()")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS product_price_summary")
