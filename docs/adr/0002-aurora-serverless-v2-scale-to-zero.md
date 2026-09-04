# ADR-0002: Aurora PostgreSQL Serverless v2 with scale-to-zero

> **Superseded by [ADR-0009](0009-neon-instead-of-aurora.md)** — the database moved to Neon; the VPC and Aurora were removed. Kept for the record.

## Context

PostgreSQL is a required skill to showcase. Options within a ~$0 budget: RDS db.t4g.micro
(Free Plan eligible, always on), Aurora Serverless v2 (0–1 ACU, pauses after idle), or
Postgres-on-Lambda hacks (not credible).

## Decision

Aurora PostgreSQL 16 Serverless v2 with `min_capacity = 0`, `max_capacity = 1`,
`seconds_until_auto_pause = 300`. IAM database authentication for all application roles; the
managed master secret is used only by `scripts/bootstrap_db.sh` over the RDS Data API. The Data
API is enabled so ad-hoc queries need no network path into the VPC.

## Consequences

- Compute cost is proportional to the ~2 minutes/day the pipeline runs (≈ $1–3/month when the
  dashboard is demoed frequently, less otherwise); storage is < 1 GiB.
- The first connection after a pause takes ~15 s; `pricepulse.db.engine.wait_for_db` retries
  `OperationalError` for up to 45 s, and the API is documented as "first request is slow".
- No stored DB passwords anywhere in the application: tokens are signed per connection with
  `rds:GenerateDBAuthToken`, TLS is `verify-full` against the bundled RDS CA.
- Single instance, single AZ, 1-day backups: the raw S3 payloads allow a full rebuild, so the
  database is treated as a derived store.
