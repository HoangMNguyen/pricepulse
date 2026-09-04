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
   which invalidates the CloudFront cache (`/*`), then builds one digest per recipient (default
   recipients + confirmed watchers, each with its unsubscribe link) and sends via SES v2.
   On failure (after 1 retry) the invocation record goes to the SNS alarms topic.
5. **Read.** CloudFront → API Gateway → `api` (FastAPI via Mangum). Read pages carry
   `Cache-Control: public, max-age=300, s-maxage=86400`, so between runs CloudFront answers from
   cache and the database stays suspended. `/v1/watches*`, `/watches/*`, `/health`, `/v1/runs*`
   bypass the cache. Every read hits the materialized summary; sort keys are index-served and
   pagination is a sort-bound keyset cursor.
6. **Watch.** `POST /v1/watches` (public) inserts an unconfirmed row and writes
   `outbox/watch_confirm/<date>/<id>.json`; the S3 event invokes `mailer`, which sends the
   confirmation. `GET /watches/confirm/<token>` confirms; only confirmed watches reach
   `classify_alerts`. `GET /watches/unsubscribe/<token>` deletes. Admin list/delete need `X-API-Key`.

## Functions

| Function | Trigger | DB user | Memory / timeout | Notes |
| --- | --- | --- | --- | --- |
| scrape | EventBridge Scheduler | — | 512 MB / 300 s | |
| process | S3 ObjectCreated | app_rw | 1024 MB / 600 s | destinations; serialized by schedule + `claim_run` |
| notify | Lambda Destination | — | 256 MB / 60 s | SES sandbox: recipients must be verified; invalidates CloudFront |
| mailer | S3 ObjectCreated (`outbox/`) | — | 256 MB / 60 s | one transactional email per object |
| api | API Gateway HTTP API | app_rw | 1024 MB / 29 s | read routes are `READ ONLY` transactions |
| migrate | manual / deploy.yml | app_migrator | 512 MB / 300 s | `alembic upgrade head` |

No function is in a VPC: the database is Neon, reached over TLS; each function reads its role's connection URL from SSM at cold start.

## Schema

- `source` — 2 rows.
- `product` — one row per retailer SKU, `UNIQUE (source_id, external_id)`, trigram index on name.
- `ingestion_run` — one row per raw object key (`UNIQUE`), status machine `running → succeeded | failed`.
- `price_observation` — `PARTITION BY RANGE (observed_at)`, monthly, PK `(product_id, observed_at)`.
- `watch` — email + per-product threshold + `token` (unique) + `confirmed_at`; partial index on
  unconfirmed rows per email backs the 5-pending limit. `alert` — what each run raised
  (`UNIQUE (run_id, product_id, kind)`).
- `product_price_summary` — materialized: latest observation, previous price, 90-day baseline,
  `discount_pct`, `savings`, `first_seen_at`; one index per sort key plus a trigram index on name.
- Helpers: `ensure_price_partition(timestamptz)`, `refresh_price_summary()`,
  `prune_price_partitions(int)` — `SECURITY DEFINER`, fixed `search_path`, EXECUTE granted to
  `app_rw` only. Partitions older than 13 months are dropped after each run.

## Failure modes

| Failure | Handling |
| --- | --- |
| Retailer 429/5xx/network error | 3 attempts with exponential backoff; the run fails loudly (alarm) rather than storing a partial payload |
| Retailer changes response shape | `parse()` raises → run `failed` with the error text; raw payload preserved for a fix + retry |
| S3 delivers the same event twice | `ingestion_run` claim returns no row → `skipped=true`, no email |
| `process` dies mid-run | row stays `running`; after 30 min the next attempt reclaims it; Lambda retries once, then `on_failure` → SNS |
| Neon compute suspended | first connection retried for up to `DB_CONNECT_WAIT_S` (`wait_for_db`); resume ≈ 0.5 s |
| Cost creep | $5 budget at 80 % actual / 100 % forecast, all logs 14-day retention; Neon Free hard-stops compute at 100 CU-h |
| Scrape silently missing or short | `no-scrape-<source>` (ScrapeRuns < 1/day, missing = breaching) and `low-products-<source>` (ProductsSeen < 100) alarms → SNS |
| Database down | API answers 503 + `Retry-After: 5` (HTML page for browsers); cached pages keep serving from CloudFront |
| Watch spam | 5 unconfirmed watches per email, unique `(product, email)`, outbox objects expire after 7 days |

## Local vs AWS

Same code, different settings: `DATABASE_URL` + `RAW_LOCAL_DIR` locally; `DATABASE_URL_SSM` +
`RAW_BUCKET` on AWS. The CLI (`pricepulse scrape|process|run|notify|mailer`) calls
the same service functions the Lambda handlers call; locally the outbox is a directory under
`RAW_LOCAL_DIR` and `PUBLIC_BASE_URL` defaults to `http://localhost:8000`.
