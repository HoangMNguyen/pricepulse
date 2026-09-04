# Architecture

## Data flow

1. **Schedule.** EventBridge Scheduler fires `scrape` at 13:00 UTC (`{"source":"ikea"}`) and
   13:10 UTC (`{"source":"uniqlo"}`).
2. **Scrape.** `Source.fetch()` walks the retailer's JSON endpoint (IKEA: one request per offer
   tag, ~1 page; UNIQLO: 4 gender paths × ≤ 100 items/page, ~18 pages). Every response is stored
   verbatim as `raw/<source>/<YYYY-MM-DD>/<HHMMSS>-<id>.json.gz` in the raw bucket.
3. **Process.** The S3 `ObjectCreated` event invokes `process`, which:
   - claims the key in `ingestion_run` (`ON CONFLICT ... WHERE status='failed' OR stale`) — a
     duplicate delivery returns `skipped=true` and does nothing;
   - parses the raw payload into `ProductSnapshot`s (pure function, unit-tested on fixtures);
   - ensures the month's partition exists, upserts `product`, reads the previous observation per
     product, inserts `price_observation` rows, classifies alerts, records them in `alert`, marks
     the run `succeeded` — all in one transaction;
   - refreshes `product_price_summary` concurrently (outside the transaction);
   - returns `{run_id, source, products_seen, observations_inserted, alerts[], skipped}`.
4. **Notify.** The Lambda Destination (`on_success`) delivers that return value to `notify`,
   which builds one digest per recipient (default recipients + watchers) and sends via SES v2.
   On failure (after 1 retry) the invocation record goes to the SNS alarms topic.
5. **Read.** API Gateway → `api` (FastAPI via Mangum). All reads hit the materialized summary;
   writes (`/v1/watches`) need `X-API-Key`.

## Functions

| Function | Trigger | VPC | DB user | Memory / timeout | Notes |
| --- | --- | --- | --- | --- | --- |
| scrape | EventBridge Scheduler | no | — | 512 MB / 300 s | |
| process | S3 ObjectCreated | yes | app_rw | 1024 MB / 600 s | destinations; serialized by schedule + `claim_run` |
| notify | Lambda Destination | no | — | 256 MB / 60 s | SES sandbox: recipients must be verified |
| api | API Gateway HTTP API | yes | app_rw | 1024 MB / 29 s | read routes are `READ ONLY` transactions |
| migrate | manual / deploy.yml | yes | app_migrator | 512 MB / 300 s | `alembic upgrade head` |

## Schema

- `source` — 2 rows.
- `product` — one row per retailer SKU, `UNIQUE (source_id, external_id)`, trigram index on name.
- `ingestion_run` — one row per raw object key (`UNIQUE`), status machine `running → succeeded | failed`.
- `price_observation` — `PARTITION BY RANGE (observed_at)`, monthly, PK `(product_id, observed_at)`.
- `watch` — email + per-product threshold; `alert` — what each run raised (`UNIQUE (run_id, product_id, kind)`).
- `product_price_summary` — materialized: latest observation + 90-day baseline + `discount_pct`.
- Helpers: `ensure_price_partition(timestamptz)`, `refresh_price_summary()` — `SECURITY DEFINER`,
  fixed `search_path`, EXECUTE granted to `app_rw` only.

## Failure modes

| Failure | Handling |
| --- | --- |
| Retailer 429/5xx/network error | 3 attempts with exponential backoff; the run fails loudly (alarm) rather than storing a partial payload |
| Retailer changes response shape | `parse()` raises → run `failed` with the error text; raw payload preserved for a fix + retry |
| S3 delivers the same event twice | `ingestion_run` claim returns no row → `skipped=true`, no email |
| `process` dies mid-run | row stays `running`; after 30 min the next attempt reclaims it; Lambda retries once, then `on_failure` → SNS |
| Aurora paused | first connection retried for up to 45 s (`wait_for_db`) |
| Cost creep | ACU > 1.5 alarm, $5 budget at 80 % actual / 100 % forecast, all logs 14-day retention |

## Local vs AWS

Same code, different settings: `DATABASE_URL` + `RAW_LOCAL_DIR` locally; `DB_HOST` +
`DB_IAM_AUTH=true` + `RAW_BUCKET` on AWS. The CLI (`pricepulse scrape|process|run|notify`) calls
the same service functions the Lambda handlers call.
