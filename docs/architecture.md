# Architecture

## Data flow

1. **Schedule.** EventBridge Scheduler fires `scrape` once per source at the cron in the
   `sources` Terraform variable (today 13:00 UTC `{"source":"ikea"}`, 13:10 UTC
   `{"source":"uniqlo"}`).
2. **Scrape.** `Source.fetch()` walks the retailer's JSON endpoint (IKEA: one discovery request
   plus one request per offer tag, ~1 page, plus up to four sort orders of the last-chance set;
   UNIQLO: 4 gender paths × ≤ 100 items/page, ~18 pages, then one per-SKU stock request per
   (productId, priceGroup) — ~1,300, tagged `role: l2s` — over 3 worker threads).
   Every response is stored verbatim as `raw/<source>/<YYYY-MM-DD>/<HHMMSS>-<id>.json.gz` in the
   raw bucket. IKEA's discovery request is tagged `role: index` and skipped by `parse()`; its
   last-chance pages are tagged `role: last_chance` and parsed like offer pages.
3. **Process.** The S3 `ObjectCreated` event invokes `process`, which:
   - upserts the adapter's `source` row (`ensure_source`: code, name, base URL) and claims the key
     in `ingestion_run` (`ON CONFLICT ... WHERE status='failed' OR running past RUN_LEASE_S`) —
     a key that already `succeeded` returns `skipped=true` and does nothing; a key another
     worker is still processing raises, so a retry never reports work it did not do;
   - parses the raw payload into `ProductSnapshot`s (pure function, unit-tested on fixtures) and
     fails the run if fewer than `MIN_PRODUCTS` came back — a broken response is never published
     as an empty catalog;
   - ensures the month's partition exists, upserts `product` (one `unnest` statement per 500
     products; current-state columns only move forward in payload time), reads the previous
     observation and 90-day peak per product **older than the payload's `fetched_at`**, inserts
     `price_observation` rows (`ON CONFLICT DO NOTHING`), classifies alerts (`price_drop` and
     `watch_hit` fire when the price fell since the previous observation, sized against the
     peak), records them in `alert`, marks the run `succeeded` — all in one transaction;
   - refreshes `product_price_summary` concurrently and prunes partitions older than
     `RETENTION_MONTHS` (autocommit, outside the transaction but inside the same failure boundary:
     an error here marks the run `failed` too);
   - returns `{run_id, source, products_seen, observations_inserted, alerts[], skipped}`.
4. **Notify.** The Lambda Destination (`on_success`) delivers that return value to `notify`,
   which invalidates the CloudFront cache (`/*`), then builds one digest per recipient (default
   recipients + confirmed watchers) and sends via SES v2, one call per recipient. After SES
   accepts a digest an empty marker `sent/<run>/<sha256(email)>` is written to the raw bucket;
   a re-invocation (crash retry, manual re-run) skips marked recipients, so nothing is resent
   except the one recipient in flight when a worker died. A rejected recipient is skipped, the
   rest still go out, and the failures are published to the alarms topic (`ALARMS_TOPIC_ARN`)
   instead of raised — a raise would retry the batch. Every digest that contains a watch hit
   carries per-product unsubscribe links in the body plus `List-Unsubscribe` /
   `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers pointing at
   `/watches/unsubscribe-all/<token>`: mail clients present that as "unsubscribe from this
   sender", so it removes every watch of the address.
5. **Read.** CloudFront → API Gateway → `api` (FastAPI via Mangum). `/` is a retailer picker (one
   card per source); `/?source=<code>` is that retailer's deals page; the switcher, stats and
   categories come from the `source` table, and the column layout (`list_price` vs `history`)
   from the adapter's `layout` (see [ui-design.md](ui-design.md)). Page CSS/JS are served from
   `/static/` (content-hashed query string), so the CSP has no `'unsafe-inline'`. `/robots.txt`
   and `/sitemap.xml` (`/`, every retailer page, every current
   product) are generated. Read pages carry `Cache-Control: public, max-age=300, s-maxage=86400`,
   so between runs CloudFront answers from cache and the database stays suspended.
   `/v1/watches*`, `/watches/*`, `/health`, `/v1/runs*` bypass the cache. Every listing read hits
   the materialized summary filtered on `is_current`; sort keys are index-served and pagination
   is a sort-bound keyset cursor. Product pages and history stay reachable for delisted items.
6. **Watch.** `POST /v1/watches` (public) inserts an unconfirmed row, **commits**, then writes
   `outbox/watch_confirm/<date>/<id>.json`; the S3 event invokes `mailer`, which sends the
   confirmation. Commit-first means a crash between the two leaves a row without mail; the next
   request for the same (product, email) re-queues the confirmation (once per 10 minutes, same
   token) instead of answering 409, so that window heals itself — the reverse order would mail
   a token that never commits. Quotas are checked under one transaction advisory lock: 5
   unconfirmed watches per email, 10 confirmation mails per hour per client (SHA-256 of the
   first `X-Forwarded-For` hop, stored on the unconfirmed row only and cleared on confirmation)
   and 30 per hour overall. The links in email render a page on GET and act on POST — mail
   scanners prefetch GETs: `GET /watches/confirm/<token>` shows a Confirm button, `POST`
   confirms; `GET /watches/unsubscribe/<token>` shows an Unsubscribe button, `POST` deletes;
   `/watches/unsubscribe-all/<token>` does the same for every watch of the address (the
   one-click target). Only confirmed watches reach `classify_alerts`. Admin list/delete need
   `X-API-Key`. A daily `backup` Lambda dumps the `watch` table to `backup/watch/<date>/` in the
   raw bucket (35-day retention); `pricepulse restore-watches` puts it back keyed on
   (source, external id, email) — see the runbook.

## Functions

| Function | Trigger | DB user | Memory / timeout | Notes |
| --- | --- | --- | --- | --- |
| scrape | EventBridge Scheduler | — | 512 MB / 900 s | `on_failure` → SNS; UNIQLO runs ~6–7 min |
| process | S3 ObjectCreated | app_rw | 1024 MB / 600 s | `on_success` → notify, `on_failure` → SNS; serialized by schedule + `claim_run` |
| notify | Lambda Destination | — | 256 MB / 60 s | SES sandbox: recipients must be verified; invalidates CloudFront; `on_failure` → SNS |
| mailer | S3 ObjectCreated (`outbox/`) | — | 256 MB / 60 s | one transactional email per object; `on_failure` → SNS |
| api | API Gateway HTTP API | app_rw | 1024 MB / 29 s | read routes are `READ ONLY` transactions |
| migrate | manual / deploy.yml | app_migrator | 512 MB / 300 s | `alembic upgrade head` |
| backup | EventBridge Scheduler (14:00 UTC) | app_rw | 256 MB / 60 s | dumps `watch` to `backup/watch/<date>/`; `on_failure` → SNS |

Every asynchronously invoked function (`scrape`, `process`, `notify`, `mailer`, `backup`) has
`maximum_retry_attempts = 1` and an `on_failure` Destination to the SNS alarms topic; `api` and
`migrate` are invoked synchronously. No function is in a VPC: the database is Neon, reached over
TLS; each function reads its role's connection URL from SSM at cold start.

## Schema

- `source` — one row per retailer adapter, created on the adapter's first run (`id` is an
  identity column since 0005; ids 1–2 were seeded).
- `product` — one row per retailer SKU, `UNIQUE (source_id, external_id)`, trigram index on name.
  `variants JSONB` (since 0006) is the retailer's current colour/size listing —
  `{"colours": [{code, name, image, chip, sizes?: [name, ...]}], "sizes": [{code, name,
  in_stock}], "lengths"?: [...], "colour_total", "stock_at"?}` — `colours[].sizes` is the
  in-stock size names for that colour and `stock_at` the payload's `fetched_at`, both present
  only when per-SKU stock was fetched (UNIQLO); without it every listed size is `in_stock`.
  NULL when the retailer exposes none (IKEA). The deals `size=` filter is jsonb containment on
  `{"sizes": [{"name", "in_stock": true}]}`; `labels JSONB` is the list of availability markers
  (`last_chance`, `in_store_only`, `xl_store_only`, `online_only`, `select_variants`,
  `coming_soon`; vocabulary in `domain/models.py`). Both are current-state columns overwritten by
  every upsert, like `name` and `image_url`, and are read through a PK join from the summary —
  they are not part of the materialized view or of `price_observation`.
- `ingestion_run` — one row per raw object key (`UNIQUE`), status machine `running → succeeded | failed`;
  `observed_at` is the payload's `fetched_at` and defines run chronology (ids only reflect
  processing order).
- `price_observation` — `PARTITION BY RANGE (observed_at)`, monthly, PK `(product_id, observed_at)`.
- `watch` — email + per-product threshold + `token` (unique) + `confirmed_at`; partial index on
  unconfirmed rows per email backs the 5-pending limit. `alert` — what each run raised
  (`UNIQUE (run_id, product_id, kind)`).
- `product_price_summary` — materialized: latest observation, previous price, 90-day baseline,
  `discount_pct`, `savings`, `first_seen_at`, `source` (code) and `is_current`; the sort/filter
  indexes are partial on `WHERE is_current`, plus the unique `product_id` index that
  `REFRESH … CONCURRENTLY` needs.
  - `is_current` means "the product's latest observation came from its source's latest
    *succeeded* run by `observed_at`". IKEA's feed is offers and last-chance items only, so an
    item that leaves the feed must leave the deals list instead of lingering with its last sale
    price. Replaying an older raw object later cannot un-current anything: chronology is the
    payload's timestamp, not the run id.
    Consequence: if an adapter ever runs more than once a day, products missing from the newer
    run become non-current until the next run that lists them — accepted.
- Helpers: `ensure_price_partition(timestamptz)`, `refresh_price_summary()`,
  `prune_price_partitions(int)` — `SECURITY DEFINER`, fixed `search_path`, EXECUTE granted to
  `app_rw` only; `prune_price_partitions` raises for `keep_months < 1`. Partitions older than
  13 months are dropped after each run.

## Retailer notes

- **UNIQLO price groups.** Prices are per `(productId, priceGroup)`: when some colours of a style
  are marked down, the retailer lists the style twice — group `00` at the regular price and group
  `01`/`02` with the clearance colours. Each group is its own product (`external_id`
  `E424873-000` for group 00, `E424873-000/01` otherwise; the URL already carries the group), so a
  style can be two rows in the deals list. Group 00's base price becomes the sibling groups'
  `list_price` — the only way UNIQLO exposes an original price; the 90-day baseline remains the
  fallback for everything else. `colors[]`/`sizes[]` on the list feed are stock-filtered by the
  retailer (a listed colour has at least one buyable SKU); `colour_total` counts the chip images,
  i.e. every colour the style has had. Size availability depends on the colour, so a second
  stage calls `/products/{id}/price-groups/{pg}/l2s?withPrices=true&withStocks=true` once per
  pair (3 threads, 0.5 s pause each ⇒ ~3.8 calls/s, ~6 min for ~1,300 pairs) and records, per
  colour, the sizes with an `IN_STOCK`/`LOW_STOCK` SKU. The l2s body carries codes only, so
  names are looked up in the list feed's stock-filtered `colors[]`/`sizes[]`: an option sold
  out in every colour has no name and is skipped. A pair whose l2s call fails after retries is
  logged and skipped (the product keeps list-level variants); `robots.txt` allows the path but
  disallows `storeId=`, which is never sent.
- **IKEA last chance and in-store offers.** The offers filter only lists Family prices;
  discontinued "while supply lasts" items come from `f-last-chance=true`. That endpoint has no
  offset parameter and its `max` under-reports, so the set is fetched as the union of up to four
  sort orders at the maximum page size (`size=1000`, ~1,900 items measured) and de-duplicated per
  `itemNo`. `badge.type == IN_STORE_OFFER_ONLY` is the only location-bound signal in the public
  API (no store id) and maps to the `in_store_only` label.
- **IKEA As-is (bargain corner) is out of scope.** The only listing is store-scoped and login-gated
  (`/circular/second-hand/`, app path `*/asisonline/*`), every unit is a one-off reserved for 48 h,
  and both the listing path and its backing API are robots-disallowed (`*/asisonline/*`,
  `*?storeId=*`, `web-api.ikea.com`). Nothing under ADR-0007 can observe it; the public
  last-chance/in-store signals above are used instead.

## Failure modes

| Failure | Handling |
| --- | --- |
| Retailer 429/500/502/503/504 or transport error | 3 attempts with exponential backoff (1–8 s); the run fails loudly (alarm) rather than storing a partial payload |
| Retailer changes response shape | `parse()` raises → run `failed` with the error text; raw payload preserved for a fix + retry |
| S3 delivers the same event twice | key already `succeeded` → `skipped=true`, no email; key still `running` inside `RUN_LEASE_S` → the invocation fails (Lambda retries once, then `on_failure` → SNS) rather than reporting a duplicate as done |
| `process` dies mid-run (timeout) | row stays `running`; `RUN_LEASE_S` equals the function timeout, so Lambda's retry (~1 min later) reclaims it |
| Older raw object processed after a newer one (delayed event, manual re-run) | run gets a higher id but an older `observed_at`; `is_current` and the product's current-state columns follow `observed_at`, so nothing is hidden or overwritten |
| Retailer answers with a structurally valid but empty/partial page | `parse()` raises on a page without `items`; below `MIN_PRODUCTS` the run fails before any write, the previous catalog stays current |
| Post-processing (refresh/prune) fails | run `failed`; Lambda retries once, the retry reclaims it, inserts nothing new (`ON CONFLICT DO NOTHING`) and recomputes the same alerts (previous observations are read `before` the payload's timestamp), so the digest still goes out |
| Neon compute suspended | first connection retried for up to `DB_CONNECT_WAIT_S` (`wait_for_db`); resume ≈ 1 s |
| Cost creep | $5 budget at 80 % actual / 100 % forecast, all logs 14-day retention; Neon Free hard-stops compute at 100 CU-h |
| Scrape silently missing or short | `no-scrape-<source>` (ScrapeRuns < 1/day, missing = breaching) and `low-products-<source>` (ProductsSeen < the source's `min_products`) alarms → SNS |
| Database down | API answers 503 + `Retry-After: 5` (HTML page for browsers); cached pages keep serving from CloudFront |
| Unhandled exception in `api` | branded 500 page for browsers, `{"detail": "internal server error"}` otherwise, `Cache-Control: no-store` |
| SES rejects one digest recipient | that recipient is skipped, the others are sent and marked in `sent/`, one SNS message lists the failures; the operator re-sends by hand (`pricepulse notify --key-json`), and marked recipients are not resent |
| Watch spam | quotas checked under a transaction advisory lock: 5 unconfirmed watches per email, `MAX_CONFIRMATIONS_PER_CLIENT_PER_HOUR` (10) per hashed client IP, `MAX_CONFIRMATIONS_PER_HOUR` (30) across all addresses (429), unique `(product, email)`, outbox objects expire after 7 days |
| Crash between watch commit and outbox publish | row exists without mail; the next request for the same (product, email) re-queues the confirmation with the same token |
| Neon project replaced by a plan / subscriber data lost | `prevent_destroy` on `neon_project.main`; daily `backup/watch/` dump (RPO 24 h) restored with `pricepulse restore-watches` (runbook) |
| Product sells out (per-SKU stock known, no size in stock) | hidden from the default deals list; `include_sold_out=true` shows it; the product page and history stay reachable |

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

- `scrape` runs 900 s with a fixed 0.5 s pause after each request ⇒ ~1,800 sequential requests,
  or ~5,000 over the 3 l2s workers, per run per source (derived); UNIQLO makes ~1,320 requests a
  run (18 list pages + one l2s call per product/price group, measured ~6 min), IKEA ~20.
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
