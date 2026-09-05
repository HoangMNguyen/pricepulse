# ADR-0001: Serverless (Lambda + API Gateway) over containers

## Context

The workload is two short daily batch jobs (~40 HTTP requests, ~1,600 rows) plus a low-traffic
read API. The budget is AWS Free Plan credits (~$0/month target). The project exists to
demonstrate production judgement for backend/database and cloud roles.

## Decision

Run everything on Lambda (Python 3.14, arm64): EventBridge Scheduler → `scrape`, S3 event →
`process`, Lambda Destination → `notify`, API Gateway HTTP API → `api` (FastAPI via Mangum),
and a manually/CI-invoked `migrate`. No ECS, no EC2, no containers in production. Docker is used
only for the local Postgres.

## Consequences

- Idle cost is zero; the whole stack is torn down with one `terraform destroy`.
- Cold starts (~1–2 s) and the database resume (≈ 1 s on Neon; was ~15 s on Aurora, see
  ADR-0009) are acceptable for a batch job and a small public API; documented in the README.
- The code is deliberately runtime-agnostic: every handler is a thin wrapper over
  `pricepulse.services`, so moving to a container is a packaging change, not a rewrite.
- Kubernetes/ECS operations are not part of this project.
