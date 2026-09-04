"""Core schema: sources, products, ingestion runs, partitioned price observations, watches, alerts.

Revision ID: 0001_schema
Revises: None
"""

from alembic import op

revision = "0001_schema"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    """
    CREATE TABLE source (
      id SMALLINT PRIMARY KEY,
      code TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      base_url TEXT NOT NULL
    )
    """,
    """
    INSERT INTO source VALUES
      (1, 'ikea', 'IKEA US', 'https://www.ikea.com/us/en/'),
      (2, 'uniqlo', 'UNIQLO US', 'https://www.uniqlo.com/us/en/')
    """,
    """
    CREATE TABLE product (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      source_id SMALLINT NOT NULL REFERENCES source(id),
      external_id TEXT NOT NULL,
      name TEXT NOT NULL,
      category TEXT,
      url TEXT NOT NULL,
      image_url TEXT,
      currency CHAR(3) NOT NULL DEFAULT 'USD',
      first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (source_id, external_id)
    )
    """,
    "CREATE INDEX product_name_trgm_idx ON product USING gin (name gin_trgm_ops)",
    """
    CREATE TABLE ingestion_run (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      source_id SMALLINT NOT NULL REFERENCES source(id),
      raw_object_key TEXT NOT NULL UNIQUE,
      started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      finished_at TIMESTAMPTZ,
      status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
      products_seen INT,
      observations_inserted INT,
      error TEXT
    )
    """,
    """
    CREATE TABLE price_observation (
      product_id BIGINT NOT NULL REFERENCES product(id),
      observed_at TIMESTAMPTZ NOT NULL,
      run_id BIGINT NOT NULL REFERENCES ingestion_run(id),
      price NUMERIC(10,2) NOT NULL CHECK (price >= 0),
      list_price NUMERIC(10,2) CHECK (list_price IS NULL OR list_price >= price),
      retailer_sale_flag BOOLEAN NOT NULL,
      retailer_tag TEXT,
      valid_to DATE,
      PRIMARY KEY (product_id, observed_at)
    ) PARTITION BY RANGE (observed_at)
    """,
    # SECURITY DEFINER: the app role (app_rw) has only DML rights; creating a partition needs
    # ownership of the parent, which belongs to the migrator. Fixed search_path per PG guidance.
    """
    CREATE OR REPLACE FUNCTION ensure_price_partition(ts TIMESTAMPTZ) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
      -- Month boundaries are fixed in UTC so the layout never depends on the server timezone.
      m TIMESTAMP := date_trunc('month', ts AT TIME ZONE 'UTC');
      s TIMESTAMPTZ := m AT TIME ZONE 'UTC';
      e TIMESTAMPTZ := (m + INTERVAL '1 month') AT TIME ZONE 'UTC';
      n TEXT := format('price_observation_%s', to_char(m, 'YYYY_MM'));
    BEGIN
      EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF price_observation FOR VALUES FROM (%L) TO (%L)',
        n, s, e
      );
    END
    $$
    """,
    "REVOKE EXECUTE ON FUNCTION ensure_price_partition(TIMESTAMPTZ) FROM PUBLIC",
    """
    DO $$
    BEGIN
      -- DML goes through the parent table, whose grants are what Postgres checks, so new
      -- partitions (owned by the definer) need no extra grants for app_rw.
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        GRANT EXECUTE ON FUNCTION ensure_price_partition(TIMESTAMPTZ) TO app_rw;
      END IF;
    END
    $$
    """,
    "SELECT ensure_price_partition(now())",
    "SELECT ensure_price_partition(now() + INTERVAL '1 month')",
    r"""
    CREATE TABLE watch (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      product_id BIGINT NOT NULL REFERENCES product(id) ON DELETE CASCADE,
      email TEXT NOT NULL CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
      min_discount_pct NUMERIC(5,1) NOT NULL DEFAULT 0
        CHECK (min_discount_pct BETWEEN 0 AND 100),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (product_id, email)
    )
    """,
    """
    CREATE TABLE alert (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      run_id BIGINT NOT NULL REFERENCES ingestion_run(id),
      product_id BIGINT NOT NULL REFERENCES product(id),
      kind TEXT NOT NULL CHECK (kind IN ('new_deal', 'price_drop', 'watch_hit', 'retailer_flag')),
      old_price NUMERIC(10,2),
      new_price NUMERIC(10,2) NOT NULL,
      discount_pct NUMERIC(5,1) NOT NULL DEFAULT 0,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (run_id, product_id, kind)
    )
    """,
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS alert",
    "DROP TABLE IF EXISTS watch",
    "DROP TABLE IF EXISTS price_observation",
    "DROP FUNCTION IF EXISTS ensure_price_partition(TIMESTAMPTZ)",
    "DROP TABLE IF EXISTS ingestion_run",
    "DROP TABLE IF EXISTS product",
    "DROP TABLE IF EXISTS source",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
