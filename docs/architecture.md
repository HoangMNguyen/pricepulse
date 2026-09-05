# Architecture

## Data flow

1. **Schedule.** EventBridge Scheduler fires `scrape` once per source at the cron in the
   `sources` Terraform variable (today 13:00 UTC `{"source":"ikea"}`, 13:10 UTC
   `{"source":"uniqlo"}`).
2. **Scrape.** `Source.fetch()` walks the retailer's JSON endpoint (IKEA: one discovery request
   plus one request per offer tag, ~1 page; UNIQLO: 4 gender paths × ≤ 100 items/page, ~18 pages).
   Every response is stored verbatim as `raw/<source>/<YYYY-MM-DD>/<HHMMSS>-<id>.json.gz` in the
   raw bucket. IKEA's discovery request is tagged `role: index` and skipped by `parse()`.
3. **Process.** The S3 `ObjectCreated` event invokes `process`, which:
   - upserts the adapter's `source` row (`ensure_source`: code, name, base URL) and claims the key
     in `ingestion_run` (`ON CONFLICT ... WHERE status='failed' OR stale`) — a duplicate delivery
     returns `skipped=true` and does nothing;
   - parses the raw payload into `ProductSnapshot`s (pure function, unit-tested on fixtures);
   - ensures the month's partition exists, upserts `product` (one `unnest` statement per 500
     products), reads the previous observation per product **older than the payload's
     `fetched_at`**, inserts `price_observation` rows (`ON CONFLICT DO NOTHING`), classifies
     alerts, records them in `alert`, marks the run `succeeded` — all in one transaction;
   - refreshes `product_price_summary` concurrently and prunes partitions older than
     `RETENTION_MONTHS` (autocommit, outside the transaction but inside the same failure boundary:
     an error here marks the run `failed` too);
   - returns `{run_id, source, products_seen, observations_inserted, alerts[], skipped}`.
4. **Notify.** The Lambda Destination (`on_success`) delivers that return value to `notify`,
   which invalidates the CloudFront cache (`/*`), then builds one digest per recipient (default
   recipients + confirmed watchers) and sends via SES v2. Every digest that contains a watch hit
   carries the watcher's unsubscribe link plus `List-Unsubscribe` and
   `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers.
5. **Read.** CloudFront → API Gateway → `api` (FastAPI via Mangum). The dashboard is one tab per
   retailer: `/` is the first source (code order), `/?source=<code>` the others; tabs, stats and
   categories come from the `source` table, and the column layout (`list_price` vs `history`)
   from the adapter's `layout`. Page CSS/JS are served from `/static/` (content-hashed query
   string), so the CSP has no `'unsafe-inline'`. `/robots.txt` and `/sitemap.xml` (every current
   product) are generated. Read pages carry `Cache-Control: public, max-age=300, s-maxage=86400`,
   so between runs CloudFront answers from cache and the database stays suspended.
   `/v1/watches*`, `/watches/*`, `/health`, `/v1/runs*` bypass the cache. Every listing read hits
   the materialized summary filtered on `is_current`; sort keys are index-served and pagination
   is a sort-bound keyset cursor. Product pages and history stay reachable for delisted items.
6. **Watch.** `POST /v1/watches` (public) inserts an unconfirmed row and writes
   `outbox/watch_confirm/<date>/<id>.json`; the S3 event invokes `mailer`, which sends the
   confirmation. The links in email render a page on GET and act on POST — mail scanners
   prefetch GETs: `GET /watches/confirm/<token>` shows a Confirm button, `POST` confirms;
   `GET /watches/unsubscribe/<token>` shows an Unsubscribe button, `POST` deletes (this POST is
   also the RFC 8058 one-click target). Only confirmed watches reach `classify_alerts`. Admin
   list/delete need `X-API-Key`.

## Functions

| Function | Trigger | DB user | Memory / timeout | Notes |
| --- | --- | --- | --- | --- |
| scrape | EventBridge Scheduler | — | 512 MB / 300 s | `on_failure` → SNS |
| process | S3 ObjectCreated | app_rw | 1024 MB / 600 s | `on_success` → notify, `on_failure` → SNS; serialized by schedule + `claim_run` |
| notify | Lambda Destination | — | 256 MB / 60 s | SES sandbox: recipients must be verified; invalidates CloudFront; `on_failure` → SNS |
| mailer | S3 ObjectCreated (`outbox/`) | — | 256 MB / 60 s | one transactional email per object; `on_failure` → SNS |
| api | API Gateway HTTP API | app_rw | 1024 MB / 29 s | read routes are `READ ONLY` transactions |
| migrate | manual / deploy.yml | app_migrator | 512 MB / 300 s | `alembic upgrade head` |

Every asynchronously invoked function (`scrape`, `process`, `notify`, `mailer`) has
`maximum_retry_attempts = 1` and an `on_failure` Destination to the SNS alarms topic; `api` and
`migrate` are invoked synchronously. No function is in a VPC: the database is Neon, reached over
TLS; each function reads its role's connection URL from SSM at cold start.

## Schema

- `source` — one row per retailer adapter, created on the adapter's first run (`id` is an
  identity column since 0005; ids 1–2 were seeded).
- `product` — one row per retailer SKU, `UNIQUE (source_id, external_id)`, trigram index on name.
- `ingestion_run` — one row per raw object key (`UNIQUE`), status machine `running → succeeded | failed`.
- `price_observation` — `PARTITION BY RANGE (observed_at)`, monthly, PK `(product_id, observed_at)`.
- `watch` — email + per-product threshold + `token` (unique) + `confirmed_at`; partial index on
  unconfirmed rows per email backs the 5-pending limit. `alert` — what each run raised
  (`UNIQUE (run_id, product_id, kind)`).
- `product_price_summary` — materialized: latest observation, previous price, 90-day baseline,
  `discount_pct`, `savings`, `first_seen_at`, `source` (code) and `is_current`; the sort/filter
  indexes are partial on `WHERE is_current`, plus the unique `product_id` index that
  `REFRESH … CONCURRENTLY` needs.
  - `is_current` means "the product's latest observation came from its source's latest
    *succeeded* run". IKEA's feed is offers-only, so an item that leaves the feed must leave the
    deals list instead of lingering with its last sale price. Consequence: if an adapter ever runs
    more than once a day, products missing from the newer run become non-current until the next
    run that lists them — accepted.
- Helpers: `ensure_price_partition(timestamptz)`, `refresh_price_summary()`,
  `prune_price_partitions(int)` — `SECURITY DEFINER`, fixed `search_path`, EXECUTE granted to
  `app_rw` only; `prune_price_partitions` raises for `keep_months < 1`. Partitions older than
  13 months are dropped after each run.

## Failure modes

| Failure | Handling |
| --- | --- |
| Retailer 429/500/502/503/504 or transport error | 3 attempts with exponential backoff (1–8 s); the run fails loudly (alarm) rather than storing a partial payload |
| Retailer changes response shape | `parse()` raises → run `failed` with the error text; raw payload preserved for a fix + retry |
| S3 delivers the same event twice | `ingestion_run` claim returns no row → `skipped=true`, no email |
| `process` dies mid-run | row stays `running`; after 30 min the next attempt reclaims it; Lambda retries once, then `on_failure` → SNS |
| Post-processing (refresh/prune) fails | run `failed`; Lambda retries once, the retry reclaims it, inserts nothing new (`ON CONFLICT DO NOTHING`) and recomputes the same alerts (previous observations are read `before` the payload's timestamp), so the digest still goes out |
| Neon compute suspended | first connection retried for up to `DB_CONNECT_WAIT_S` (`wait_for_db`); resume ≈ 1 s |
| Cost creep | $5 budget at 80 % actual / 100 % forecast, all logs 14-day retention; Neon Free hard-stops compute at 100 CU-h |
| Scrape silently missing or short | `no-scrape-<source>` (ScrapeRuns < 1/day, missing = breaching) and `low-products-<source>` (ProductsSeen < the source's `min_products`) alarms → SNS |
| Database down | API answers 503 + `Retry-After: 5` (HTML page for browsers); cached pages keep serving from CloudFront |
| Unhandled exception in `api` | branded 500 page for browsers, `{"detail": "internal server error"}` otherwise, `Cache-Control: no-store` |
| Watch spam | 5 unconfirmed watches per email, unique `(product, email)`, outbox objects expire after 7 days |

## Adding a retailer

One adapter and one Terraform map entry; nothing else in the tree names a retailer.

1. **Adapter.** Add `src/pricepulse/sources/<code>.py` implementing `fetch(client, settings)`
   and `parse(raw)` plus the metadata the rest of the system reads: `code`, `name`, `base_url`
   and `layout` (`"list_price"` when the retailer publishes list prices and offer windows,
   `"history"` when it only flags sales and the baseline is our 90-day history). Register it in
   `sources/__init__.py`, record a fixture with `scripts/record_fixtures.py`, and unit-test
   `parse` on it. Honour the retailer's `robots.txt` (see ADR-0007).
2. **Infra.** Add the code to the `sources` Terraform variable (`infra/envs/dev/variables.tf`
   default, or override it in `dev.auto.tfvars`) with its `schedule` cron and `min_products`
   alarm threshold.
3. **Deploy.** The `source` row is created by `ensure_source` on the first run; the dashboard tab,
   stats, categories, sitemap entries, alarms and schedule follow from the data. The base `.badge`
   style applies to any new code; add a brand colour in `api/static/app.css` if wanted.

Capacity (measured on the live stack where noted, otherwise derived):

- `scrape` runs 300 s with a fixed 0.5 s pause after each request ⇒ at most ~400 requests per run
  per source (derived); both retailers together make ~40 requests a day today (measured).
- The raw payload is assembled in memory before upload (512 MB function) — fine for tens of MB of
  JSON, not for image bodies.
- Neon Free storage is 0.5 GB; at roughly 60–100 bytes per `price_observation` row including
  indexes that is 5–8 M observations, i.e. about 10 retailers of ~1.5k products at 13-month
  retention (derived; two retailers currently use ~35 MB, measured in the Neon console).
- The summary refresh is a per-product `LATERAL` over 90 days of observations and scales roughly
  linearly with product count — fine to tens of thousands of products, re-measure beyond that
  (derived; not yet measured past 1.6k products).

## Local vs AWS

Same code, different settings: `DATABASE_URL` + `RAW_LOCAL_DIR` locally; `DATABASE_URL_SSM` +
`RAW_BUCKET` on AWS. `PRICEPULSE_ENV` is `local|test|dev|prod`; `local`/`test` use a pooled
engine, every other stage is Lambda and uses `NullPool` (one connection per invocation). The CLI
(`pricepulse scrape|process|run|notify|mailer`) calls the same service functions the Lambda
handlers call; locally the outbox is a directory under `RAW_LOCAL_DIR` and `PUBLIC_BASE_URL`
defaults to `http://localhost:8000`.
