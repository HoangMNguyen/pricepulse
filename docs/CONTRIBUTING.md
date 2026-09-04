# Contributing conventions

- Commits: Conventional Commits — `feat:`, `fix:`, `infra:`, `docs:`, `test:`, `chore:`.
- Architecture decisions: `docs/adr/NNNN-title.md`, Nygard template (Context / Decision / Consequences).
- Migrations: hand-written SQL via `op.execute`; SQL keywords uppercase; never `--autogenerate`.
- Lambda handlers: at most 30 lines; delegate to `pricepulse.services`. No business logic in handlers or API routes.
- Money: `decimal.Decimal` in Python, `NUMERIC(10,2)` in PostgreSQL. Never floats.
- Time: UTC everywhere; `TIMESTAMPTZ` columns; `datetime.now(UTC)` in code.
- Tooling: `uv` for envs, `ruff` for lint/format (line length 100), `pytest` with the `integration` marker for tests that need Postgres.
- Before pushing: `make lint test`.
