# ADR-0004: Monthly range partitioning and a materialized summary view

## Context

`price_observation` is append-only time series (~1,600 rows/day, ~600k/year). The read side
needs "current price, reference price, discount %" per product with sorting by discount — a
query that touches the latest row per product plus a 90-day aggregate.

## Decision

- `price_observation` is `PARTITION BY RANGE (observed_at)` with monthly partitions created
  on demand by `ensure_price_partition(ts)`; boundaries are fixed in UTC so the layout is
  independent of the server timezone.
- `product_price_summary` is a materialized view (one row per product) refreshed
  `CONCURRENTLY` at the end of every ingestion run. Reference price = retailer list price when
  present, else the 90-day `mode()` of prior observations (ties → lowest, i.e. conservative).
  The baseline excludes the current observation so a fresh drop is measured against what the
  item used to cost.
- Both DDL-ish operations are exposed as `SECURITY DEFINER` functions with a fixed
  `search_path`, because the app role has DML rights only (see `scripts/bootstrap_db.sh`).

## Consequences

- At this volume partitioning is demonstrative, but it makes retention a `DROP TABLE` and keeps
  the latest-row lookup (`ORDER BY observed_at DESC LIMIT 1` per product) within one partition.
- Deals queries are index-only over `(discount_pct DESC, product_id)`; keyset pagination is
  stable across refreshes because the sort key is materialized.
- The summary is eventually consistent by design: it reflects the last completed run.
