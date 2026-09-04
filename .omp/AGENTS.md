# PricePulse

IKEA US + UNIQLO US price tracker. Python 3.14, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16 / Aurora Serverless v2, AWS Lambda (arm64) + API Gateway + EventBridge Scheduler + S3 + SES, Terraform >= 1.10.

## Layout

- `src/pricepulse/domain` — pure dataclasses and pricing math; no I/O.
- `src/pricepulse/sources` — retailer adapters (`fetch` = network, `parse` = pure).
- `src/pricepulse/storage/raw.py` — raw JSON store (local dir or S3).
- `src/pricepulse/db` — engine (IAM auth on AWS), ORM models, repository queries.
- `src/pricepulse/services` — ingest (ETL + alerts), digest, mail.
- `src/pricepulse/api` — FastAPI app, routes, HTMX templates.
- `src/pricepulse/lambda_handlers` — thin Lambda entrypoints (<= 30 lines each).
- `alembic/versions` — hand-written SQL migrations.
- `infra/` — Terraform (`bootstrap/` state bucket, `modules/`, `envs/dev/`).
- `tests/unit`, `tests/integration` (marker `integration` needs Postgres on :5433).

## Commands

`make up` (Postgres via compose), `make migrate`, `make test`, `make lint`, `make fmt`, `make serve`, `make build`, `make tf-plan`.
CLI: `uv run pricepulse scrape|process|run|notify|migrate|serve`.

## Conventions

Conventional Commits. ADRs in `docs/adr/`. Money is `Decimal` / `NUMERIC(10,2)`; timestamps are UTC `TIMESTAMPTZ`. No business logic in handlers or routes. SQL keywords uppercase in migrations. Run `make lint test` before finishing.
