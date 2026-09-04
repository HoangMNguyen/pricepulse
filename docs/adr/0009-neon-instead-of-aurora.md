# ADR-0009: Neon (serverless PostgreSQL) instead of Aurora Serverless v2

Supersedes the database and networking parts of ADR-0002 and ADR-0003; updates the cost row of
ADR-0008.

## Context

The first deployment ran on Aurora Serverless v2 scaling to 0 ACU. Measured on the live stack:
the cluster resumed from pause in ~5–15 s, so the first request after five idle minutes took
~5 s end to end; keeping it warm (0.5 ACU) would cost ≈ $44/month against a < $5/month budget.
Because Aurora must live in a VPC, the stack also carried a private-only VPC, security groups,
an S3 gateway endpoint, IAM database authentication and the Data API — none of which serve the
product, only the choice of database. The production target for the site is sub-second responses
at ≈ $0.

## Decision

Move the database to **Neon Free** (PostgreSQL 16, `aws-us-east-1`, 0.25 CU fixed, suspend after
5 idle minutes, resume in ≈ 0.5 s). The Neon project is managed by Terraform
(`kislerdm/neon` provider); its default role `app_migrator` owns the schema. Least-privilege
`app_rw` / `app_ro` roles are created with SQL (`scripts/bootstrap_db.sh`) because roles created
through Neon's API inherit `neon_superuser`. Connection URLs live in SSM Parameter Store
SecureString parameters, one per role; each Lambda reads its own at cold start
(`ssm:GetParameter` + `kms:Decrypt` on the default `aws/ssm` key only). TLS is `sslmode=require`
with `channel_binding=require` (SCRAM channel binding blocks MITM without a pinned CA). Every
function uses the direct (non-pooler) endpoint: Neon's pooler runs PgBouncer in transaction mode,
which does not support the session-level `READ ONLY` transactions and `AUTOCOMMIT` refresh path
the app relies on, and Lambda concurrency is capped at 10 by the account quota anyway.

Removed: the VPC and all networking, the Aurora cluster/instance/subnet group, the managed master
secret, IAM DB tokens and the bundled RDS CA, the Data API, the ACU alarm.

Free-plan limits met during the cutover: `suspend_timeout_seconds` cannot be set (the default is
the 300 s we wanted) and `history_retention_seconds` is capped at 21600; projects are scoped to an
organization (`org_id`). The Terraform config encodes all three.

## Consequences

- Cost: $0 for the database (100 CU-hours/month included; the pipeline plus dashboard use ≈ 15–20),
  $0 for secrets (standard SSM parameters). Total stack ≈ $0.50/month (Route 53 zone).
- Latency: ≈ 0.5 s resume instead of 5–15 s; no user-visible "waking" state.
- Limits accepted: 0.5 GB storage (≈ 150 MB/year at 1,600 observations/day, pruned after
  13 months), no SLA, 1-day restore window. Raw payloads in S3 remain the source of truth and can
  rebuild the database from scratch (`process` is replayable per key).
- Portfolio: the Aurora / IAM-auth / private-VPC line items are replaced by "cost- and
  latency-driven database migration with measured evidence", SSM-backed secrets, and least-privilege
  roles on a third-party managed PostgreSQL. All PostgreSQL work (partitioning, materialized
  summary, `SECURITY DEFINER` helpers, role model) is unchanged.
- Terraform state now contains database credentials; the state bucket is private and SSE-S3
  encrypted (it already held the API key).
