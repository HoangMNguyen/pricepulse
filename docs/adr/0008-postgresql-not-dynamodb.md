# ADR-0008: PostgreSQL (Aurora Serverless v2), not DynamoDB

## Context

The workload is small and its access patterns are fully known: ~1,600 append-only price
observations per day; reads are top-N products by discount with a few filters, one product's
history over a time range, per-source counts, and tiny watch/run tables. That profile fits
DynamoDB well, and DynamoDB would be cheaper and operationally simpler than Aurora. The
question was raised after the first deployment; this records the comparison and the decision.

Measured on the deployed stack: Aurora runs at 0.5–1.0 ACU for ~7 minutes per pipeline run and
pauses to 0 otherwise; the first API request after a pause takes ~5 s.

## Comparison

| | Aurora Serverless v2 (chosen) | DynamoDB on-demand |
| --- | --- | --- |
| Monthly cost | ≈ $1.2–3.4 (ACU-hours, $0.40 managed secret, storage) | ≈ $0.03 (50k writes/month; storage inside the always-free 25 GB) |
| Networking | VPC, private subnets, security groups, S3 gateway endpoint; functions split in/out of the VPC with Lambda Destinations bridging them (ADR-0003) | None; every function outside a VPC with an IAM policy on the table |
| Auth & roles | IAM DB tokens, `verify-full` TLS, `app_migrator`/`app_rw`/`app_ro`, `SECURITY DEFINER` helpers | IAM on the table (optionally key-scoped conditions) |
| Schema evolution | Alembic migrations, partitions, materialized view, `migrate` Lambda | No migrations; key design fixed up front |
| Cold path | ~15 s Aurora resume + Lambda init | Lambda init only (milliseconds to the store) |
| Deals: order by discount + filters | Indexed materialized view | GSI `PK=source|ALL`, `SK=discount_pct#product_id`; `min_discount` as a range; `flagged_only` via sparse GSI or filter |
| History for one product | Range scan on `(product_id, observed_at)` partition | Natural fit: `PK=PRODUCT#id`, `SK=OBS#ts` |
| 90-day mode / min / max baseline | One SQL statement (`mode() WITHIN GROUP`, `LATERAL`), refreshed concurrently, redefinable without touching data | Computed in application code at write time and denormalized into the product item; changing the definition means a backfill job |
| Name search | `ILIKE` on a trigram index | Not supported; `Scan` + `contains` works at 1.6k items and stops working at scale, or add OpenSearch |
| Ad-hoc questions | `psql` / Data API, any query | Export to S3 + Athena |
| Idempotent run claim | `INSERT … ON CONFLICT … WHERE status = 'failed'` | `PutItem` with `ConditionExpression` (arguably cleaner) |
| What it demonstrates | Relational design, partitioning, materialized views, role/privilege model, IAM DB auth, private networking | Single-table modelling, GSIs, conditional writes |

## Decision

Keep PostgreSQL on Aurora Serverless v2.

The deciding factor is the project's purpose, not the workload: it exists to demonstrate
relational design and operations for backend/database roles. DynamoDB would remove roughly
$2/month and a third of the infrastructure — and with it the partitioning, materialized summary,
privilege model, IAM authentication and VPC design that are the substance of the portfolio.
Secondary reasons: the discount definition lives in one SQL statement and can be changed without
a backfill; name search and ad-hoc analysis come for free.

## Consequences

- Accepted: ~$1–3/month instead of ~$0; a ~5 s first request after idle; VPC plumbing.
- The store is behind `pricepulse.db.repo` and `pricepulse.api.queries`; a DynamoDB backend
  could be added behind those interfaces if a target role calls for it.
- If cost ever mattered more than the showcase, the cheapest credible move is not DynamoDB but
  turning the schedules off (storage + secret ≈ $0.50/month) or `terraform destroy`.
