# Contributing

## Set up

Prerequisites: [`uv`](https://docs.astral.sh/uv/), Docker with the compose plugin, `make`
(`psql` is optional). Neither AWS credentials nor a Neon account are needed for local work.

```bash
git clone https://github.com/HoangMNguyen/pricepulse && cd pricepulse
uv sync                    # Python 3.14 virtualenv with the dev group
uv run pre-commit install  # ruff + terraform fmt on every commit
cp .env.example .env
make up migrate            # Postgres 16 on :5433 via compose, then alembic upgrade head
make test-unit             # fast: no Postgres needed
make test                  # unit + integration (needs `make up`)
```

## Pull requests

- Commits: Conventional Commits — `feat:`, `fix:`, `infra:`, `docs:`, `test:`, `chore:`.
- `make lint test` must be green before pushing; CI runs the same commands plus
  `terraform validate` and checkov on `infra/`.
- Architectural changes come with an ADR in `docs/adr/NNNN-title.md` (Nygard template:
  Context / Decision / Consequences) and, when they change what runs where, an update to
  `docs/architecture.md`.
- Adding a retailer follows the recipe in `docs/architecture.md` ("Adding a retailer").

## Conventions

- Migrations: hand-written SQL via `op.execute`; SQL keywords uppercase; never `--autogenerate`.
  Squashing existing revisions is off the table once they are applied to the deployed database.
- Lambda handlers: at most 30 lines; delegate to `pricepulse.services`. No business logic in
  handlers or API routes; SQL lives in `pricepulse.db` (ingestion) and `pricepulse.api.queries`
  (read model), always as explicit `text()` statements — there is no ORM.
- Money: `decimal.Decimal` in Python, `NUMERIC(10,2)` in PostgreSQL. Never floats.
- Time: UTC everywhere; `TIMESTAMPTZ` columns; `datetime.now(UTC)` in code.
- Tooling: `uv` for envs, `ruff` for lint/format (line length 100), `pytest` with the
  `integration` marker for tests that need Postgres.
- Templates: no inline `<style>`, `<script>` or `on*=` handlers — the CSP has no
  `'unsafe-inline'`; page CSS/JS live in `src/pricepulse/api/static`.
