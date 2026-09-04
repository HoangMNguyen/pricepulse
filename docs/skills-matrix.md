# Skills matrix

What a mid-level Backend/Database Engineer or Cloud Engineer posting asks for, and where this repo shows it.

| Area | Skill | Where |
| --- | --- | --- |
| Python | Typed Python 3.14, uv packaging, pydantic v2 settings/models, httpx, pytest | [`pyproject.toml`](../pyproject.toml), [`src/pricepulse`](../src/pricepulse), [`tests`](../tests) |
| API | FastAPI, OpenAPI, dependency injection, keyset pagination, API-key auth, error mapping | [`api/queries.py`](../src/pricepulse/api/queries.py), [`api/routes`](../src/pricepulse/api/routes), [`api/deps.py`](../src/pricepulse/api/deps.py) |
| PostgreSQL design | Normalized schema, CHECK constraints, identity columns, `NUMERIC` money, `TIMESTAMPTZ` | [`alembic/versions/0001_schema.py`](../alembic/versions/0001_schema.py) |
| PostgreSQL performance | Declarative range partitioning, materialized view + `REFRESH CONCURRENTLY`, `DISTINCT ON`, `mode()` ordered-set aggregate, LATERAL joins, trigram index | [`0002_summary.py`](../alembic/versions/0002_summary.py), [`db/repo.py`](../src/pricepulse/db/repo.py) |
| PostgreSQL ops | Role separation, `SECURITY DEFINER` helpers with fixed `search_path`, IAM auth, Alembic discipline, idempotent `ON CONFLICT` upserts | [`scripts/bootstrap_db.sh`](../scripts/bootstrap_db.sh), [`tests/integration/test_roles.py`](../tests/integration/test_roles.py) |
| SQLAlchemy | 2.0 Core, engine lifecycle in Lambda, `do_connect` token injection, `verify-full` TLS | [`db/engine.py`](../src/pricepulse/db/engine.py) |
| Data pipeline | Raw → curated ETL, idempotent reprocessing, fixtures recorded from live APIs | [`services/ingest.py`](../src/pricepulse/services/ingest.py), [`storage/raw.py`](../src/pricepulse/storage/raw.py), [`scripts/record_fixtures.py`](../scripts/record_fixtures.py) |
| AWS compute | Lambda (arm64, layers, VPC vs non-VPC, Destinations, reserved concurrency), API Gateway HTTP API, EventBridge Scheduler | [`infra/envs/dev/functions.tf`](../infra/envs/dev/functions.tf), [`api.tf`](../infra/envs/dev/api.tf), [`scheduling.tf`](../infra/envs/dev/scheduling.tf) |
| AWS data | Aurora PostgreSQL Serverless v2 scale-to-zero, Data API, S3 events + lifecycle | [`database.tf`](../infra/envs/dev/database.tf), [`storage.tf`](../infra/envs/dev/storage.tf) |
| AWS networking | Private-only VPC, S3 gateway endpoint, SG-to-SG and prefix-list rules | [`network.tf`](../infra/envs/dev/network.tf), [ADR-0003](adr/0003-vpc-without-nat-and-lambda-destinations.md) |
| AWS security | Per-function IAM, `rds-db:connect` per DB user, OIDC for CI, encryption at rest, SES sandbox | [`iam_github.tf`](../infra/envs/dev/iam_github.tf), [`modules/lambda_function`](../infra/modules/lambda_function) |
| AWS ops | SES, SNS, CloudWatch alarms, Budgets, Powertools structured logs + EMF metrics | [`observability.tf`](../infra/envs/dev/observability.tf), [`lambda_handlers`](../src/pricepulse/lambda_handlers) |
| IaC | Terraform ≥ 1.10, S3 backend with native lockfile, modules, checkov (0 failures, every skip justified) | [`infra`](../infra) |
| CI/CD | GitHub Actions with a Postgres service container; plan/apply via OIDC; migration gate | [`.github/workflows`](../.github/workflows) |
| Reliability | Retries with backoff, idempotency keys, stale-run reclaim, on-failure destination, runbook | [`sources/http.py`](../src/pricepulse/sources/http.py), [`db/repo.py`](../src/pricepulse/db/repo.py), [`RUNBOOK.md`](RUNBOOK.md) |
| Cost engineering | Documented cost table, scale-to-zero DB, no NAT, budget alarm | [README](../README.md#cost), [ADR-0002](adr/0002-aurora-serverless-v2-scale-to-zero.md) |
| Communication | ADRs, architecture doc, OpenAPI, this matrix | [`docs`](.) |
