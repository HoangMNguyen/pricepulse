# PricePulse

IKEA US + UNIQLO US price tracker. Python 3.14, FastAPI, SQLAlchemy 2.0 Core, Alembic, PostgreSQL 16 on Neon (serverless; per-role connection URLs in SSM SecureString), AWS Lambda (arm64) + API Gateway + CloudFront + EventBridge Scheduler + S3 + SES, Terraform >= 1.10.

## Layout

- `src/pricepulse/domain` — pure dataclasses and pricing math; no I/O.
- `src/pricepulse/sources` — retailer adapters (`fetch` = network, `parse` = pure) carrying their own metadata (`code`, `name`, `base_url`, `layout`); the `source` row is created on the adapter's first run.
- `src/pricepulse/storage` — raw JSON store and outbox (local dir or S3).
- `src/pricepulse/db` — engine (URL from `DATABASE_URL` or SSM; NullPool on Lambda), `repo.py` ingestion SQL, `watches.py` watch SQL; no ORM — all SQL is explicit `text()`.
- `src/pricepulse/services` — `ingest.py` (ETL + alerts), `digest.py`, `mail.py`, `notify.py` (CDN invalidation + digests), `watches.py` (watch request workflow).
- `src/pricepulse/api` — FastAPI app, routes, `queries.py` read model, HTMX templates, `static/` (served at `/static/`; no inline CSS/JS — the CSP has no `'unsafe-inline'`).
- `src/pricepulse/lambda_handlers` — thin Lambda entrypoints (<= 30 lines each).
- `alembic/versions` — hand-written SQL migrations.
- `infra/` — Terraform (`bootstrap/` state bucket, `modules/`, `envs/dev/`).
- `tests/unit`, `tests/integration` (marker `integration` needs Postgres on :5433).

## Commands

`make up` / `make down` (Postgres via compose), `make migrate`, `make test`, `make test-unit` (no Postgres), `make lint`, `make fmt`, `make serve`, `make build`, `make tf-plan`, `make tf-apply`.
CLI: `uv run pricepulse scrape|process|run|notify|mailer|migrate|serve`.

## Conventions

Conventional Commits. ADRs in `docs/adr/`. Money is `Decimal` / `NUMERIC(10,2)`; timestamps are UTC `TIMESTAMPTZ`. No business logic in handlers or routes. SQL keywords uppercase in migrations. Run `make lint test` before finishing.
