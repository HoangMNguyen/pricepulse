# PricePulse codebase review

Date: 2026-09-07

## Overall assessment

Keep the architecture; fix the trust boundaries and failure paths before adding features. The repository is reasonably structured—not a rewrite candidate. The most consequential problems are deployment security, ingestion recovery, and incorrect alert semantics.

Reviewed adapters, domain logic, services, persistence, migrations, API/frontend, Terraform, deployment scripts, workflows, tests, README, and operational documentation. No implementation files were changed during the review.

“Reproduced” means exercised locally or observed in the live browser. **[INFERENCE]** identifies consequences traced through code but not exercised against AWS or PostgreSQL. File references describe the repository at review time.

## Highest-priority findings

### 1. Privileged deployment can execute an untrusted CI commit

**High — security · [INFERENCE]**

**Locations:** `.github/workflows/deploy.yml:3–7,25–41`; `.github/workflows/ci.yml:3–6`

The deployment workflow:

- Runs after successful `ci`, which also runs on pull requests.
- Checks out `workflow_run.head_sha`.
- Acquires AWS credentials before executing that commit’s build scripts.
- Exposes `NEON_API_KEY` to the entire plan job.
- Checks only CI success—not that the event was a trusted push from this repository.

A fork PR with a matching branch name can satisfy the branch filter. The `apply` environment approval does not protect the earlier `plan` job.

**Recommendation:** restrict deployment to successful `push` events from this repository’s `main`, or use a single trusted push workflow with test/build/deploy dependencies. Keep credentials out of build steps.

GitHub explicitly warns about this [workflow_run trust-boundary problem](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run). No exploit was attempted.

### 2. The deploy role’s name-scoped IAM permissions do not contain its privileges

**High — security · [INFERENCE]**

**Location:** `infra/envs/dev/iam_github.tf:48–60`

`PowerUserAccess` plus `iam:*` on `pricepulse-*` roles allows the deploy identity to create or modify a matching role, give it broader permissions, and assume it. The role-name prefix is not an effective permissions boundary.

This compounds finding 1. The README acknowledges broad deployment permissions, but understates the escalation risk.

**Recommendation:** constrain role/policy management and enforce a permissions boundary the deploy role cannot remove or modify.

### 3. Watch alerts calculate the wrong percentage

**High — correctness · Reproduced**

**Location:** `src/pricepulse/services/ingest.py:127–155`

The code tests whether the price decreased, but measures that decrease against `list_price` when available.

| Previous price | New price | List price | Watch threshold | Result |
| --- | --- | --- | --- | --- |
| $80 | $79 | $100 | 10% | Sends a watch hit reporting 21% |

The actual decrease is 1.25%. This contradicts the product page’s “email me when it drops by at least” promise.

**Recommendation:** calculate `price_drop` and `watch_hit` against the previous price. Keep retailer discount and price-drop percentage separate. The classification tests do not cover a previous observation combined with a list price.

### 4. A timed-out ingestion can have its retry incorrectly treated as success

**High — reliability · [INFERENCE]**

**Locations:** `src/pricepulse/db/repo.py:51–69`; `src/pricepulse/services/ingest.py:169–174`; `infra/envs/dev/functions.tf:85,96`

A timeout or abrupt process termination leaves the run `running`. The automatic retry arrives before the 30-minute reclaim window, so `claim_run()` returns `None`. Ingestion interprets this as an already-completed duplicate and returns `skipped=True`.

There is no subsequent automatic retry scheduled for the reclaim window.

The initial timeout can still trigger the Lambda Errors alarm, but the retry does not recover the run, and its successful return prevents the failure-destination path.

**Recommendation:** distinguish “already succeeded” from “still running”; define lease/recovery behavior consistent with Lambda execution and retry timing. Include stuck `running` records in operator recovery queries.

### 5. Out-of-order ingestion can hide a retailer’s current catalog

**High — data consistency · [INFERENCE]**

**Locations:** `alembic/versions/0005_summary_v3.py:18–25,53`; `src/pricepulse/db/repo.py:94–97`

“Latest successful run” is selected by run ID, while latest product observations are selected by observation timestamp.

If an older, previously unclaimed payload is processed after a newer payload:

- The old payload receives the greater run ID.
- Current observations still belong to the newer payload.
- Products fail the `is_current` comparison and disappear from listings.

Separately, stale payloads unconditionally overwrite current names, images, variants, and stock labels.

**Recommendation:** persist the payload timestamp on runs and use it for chronology. Guard current-state product updates against older observations. Processing order must not define data freshness.

### 6. An incomplete IKEA response can be published as an empty successful catalog

**High — data integrity**

**Locations:** `src/pricepulse/sources/ikea.py:92–95`; `src/pricepulse/services/ingest.py:180–193`

**Reproduced:** a response declaring `max=100, end=100` but missing its `items` array parses successfully into `[]`.

There is no completeness check before marking ingestion successful and refreshing the summary. **[INFERENCE]** Publishing that run makes the preceding catalog non-current.

The low-product alarm detects the problem after publication; it does not preserve the last good catalog.

**Recommendation:** reject malformed response structures and validate completeness before publishing a snapshot. Distinguish a valid empty catalog from a broken response.

## Other substantive findings

### 7. Public API docs are blank

**Medium · Reproduced in the live browser**

**Locations:** `infra/envs/dev/cdn.tf:106–117`; `src/pricepulse/api/app.py:72–76`

Live `/docs` returns HTTP 200 with an empty Swagger container. The browser console confirms CSP blocks its inline bootstrap.

**Recommendation:** serve a CSP-compatible documentation page with an external bootstrap script. Do not add `'unsafe-inline'`.

### 8. Stock presentation contradicts itself

**Medium · Observed in the live browser**

**Locations:** `src/pricepulse/api/templates/product.html:42–56`; `src/pricepulse/api/static/app.js:95–108`

At review time, `/products/2853` said “2 of 17 available” while the selected colour had “0 of 7 sizes”; it still presented “Buy this colour.”

**Recommendation:** derive availability counts from per-SKU stock when present. Distinguish listed colours, in-stock colours, and unknown availability.

### 9. Malformed numeric filters produce HTTP 500

**Medium · Reproduced**

**Location:** `src/pricepulse/api/routes/dashboard.py:50–60`

Local HTTP requests with the database dependency replaced to isolate request validation produced:

- `min_price=bad` → 422.
- `min_price=NaN` → 500.
- `min_price=sNaN` → 500.

Direct helper execution also accepted `Infinity`.

**Recommendation:** reject non-finite Decimals before comparisons. Align HTML-filter validation with the JSON API.

### 10. Digest retries resend successful deliveries

**Medium · Reproduced with an isolated SES failure simulation**

**Location:** `src/pricepulse/services/mail.py:85–105`

A second-recipient failure followed by retry produced deliveries in this order: `first, first, second`. Confirmations similarly have no delivery checkpoint.

**Recommendation:** add durable per-message/recipient delivery state and explicit at-least-once semantics. Account for the unavoidable ambiguity if SES accepts mail just before a worker dies.

### 11. Public watch creation lacks a meaningful abuse budget

**Medium · [INFERENCE]**

**Locations:** `src/pricepulse/services/watches.py:35–43`; `src/pricepulse/db/watches.py:66–86`

The five-pending limit is per email, and its count-then-insert check is not concurrency-safe. It does not bound confirmation mail sent across arbitrary addresses.

**Recommendation:** add per-client and global confirmation-mail limits; enforce the email quota atomically. API Gateway’s overall throttle is not a mail-abuse control.

### 12. Subscriber recovery is incomplete

**Medium · [INFERENCE]**

**Location:** `infra/envs/dev/database.tf:6–24`

Neon retention is six hours, with no database backup/restore workflow in the reviewed infrastructure. Raw retailer payloads cannot reconstruct watch confirmations and tokens.

**Recommendation:** define acceptable data loss, protect the persistent database against accidental replacement, and establish a tested subscriber-data restore path.

### 13. IKEA’s informational NEW_PRODUCT tag becomes a sale flag through the legacy fallback

**Low–medium · Reproduced**

**Location:** `src/pricepulse/sources/ikea.py:122–127`

Both `{"tag": "NEW_PRODUCT"}` and `{"tags": ["NEW_PRODUCT"], "tag": "NEW_PRODUCT"}` return `NEW_PRODUCT` from `_price_tag()`. The array path filters the informational tag, but the fallback restores it.

**Recommendation:** apply the informational-tag exclusion to both representations.

### 14. Watcher-only email subjects say “0 new deals, 0 price drops”

**Low · Reproduced**

**Location:** `src/pricepulse/services/digest.py:35–40`

A watch-hit digest addressed only to a watcher generates a subject describing zero changes, because the subject ignores `watch_hits`.

**Recommendation:** make the subject describe watched-product changes.

## Operational issues worth addressing next

### The outbox is not transactional with the watch insert

**Locations:** `src/pricepulse/services/watches.py:42–57`; `src/pricepulse/api/deps.py:20–22`

S3 publication happens before the enclosing database commit. A failed commit can leave an email pointing to a nonexistent watch. **[INFERENCE]**

If stronger guarantees are needed, persist the outbox record in the same database transaction. Merely reversing the write order exchanges one failure window for another.

### Database waits are not bounded at the connection level

**Location:** `src/pricepulse/db/engine.py:30–48`

`stop_after_delay()` cannot interrupt an individual connection attempt. No explicit connection timeout is configured. **[INFERENCE]**

### Password rotation does not refresh existing engines

**Location:** `src/pricepulse/db/engine.py:26,51–58`

The SSM cache’s five-minute lifetime is misleading here: URL resolution happens when the singleton engine is created. Warm processes retain that URL until the engine is replaced or the process recycled. **[INFERENCE]**

### Migration invocation has mismatched timeouts

**Location:** `.github/workflows/deploy.yml:114–123`

The migration Lambda allows 300 seconds, but deployment does not override the AWS CLI read timeout or serialize concurrent migration invocations. **[INFERENCE]**

## Codebase and structure review

### Keep these choices

- `fetch()` versus pure `parse()` is the right adapter boundary.
- Explicit SQL and small repository functions make transactions and database behavior inspectable.
- Thin Lambda handlers keep infrastructure wiring out of application logic.
- Decimal money, parameterized SQL, keyset pagination, and read-only API transactions are sensible foundations.
- GET prompts / POST mutations for confirmation and unsubscribe correctly account for email-link scanners.
- Per-function IAM and per-role database URLs are substantially better scoped than the deployment role.

Do not introduce an ORM, generic repository framework, plugin framework, or frontend rewrite merely to address this review.

### Targeted structural improvements

1. **Separate pricing concepts explicitly.** Retailer discount, historical discount, and change since the previous observation currently share terminology that concealed finding 3.
2. **Give ingestion explicit publication semantics.** Validating a snapshot, persisting observations, publishing current state, and completing notification delivery are distinct states—not interchangeable meanings of “succeeded.”
3. **Reduce presentation coupling.** `api/queries.py` mixes SQL, cursor handling, display labels, and derived presentation fields. `dashboard.py` owns CDN configuration consumed by app-level error rendering. Small extractions are sufficient.
4. **Type the evolving stock contract.** `variants: dict` crosses adapter, database, API, templates, and JavaScript. Its stock/unknown/absent semantics now warrant a defined structure.
5. **Do not optimize speculative scale first.** Address chronology and failure recovery before adding indexes, more concurrency, or more infrastructure.

## Test review

The suite covers useful behavior, but green tests currently overstate confidence in failure handling.

Important gaps:

- Previous price plus list price in watch classification.
- Abrupt termination and `running`-claim collisions.
- Older payload arriving after newer data.
- Malformed/partial snapshots before publication.
- Partial email delivery followed by retry.
- Actual browser execution under the production CSP.

### Role tests mask part of the production contract

`tests/integration/test_roles.py:21–39` manually grants helper permissions that production relies on migrations to grant. A broken migration grant can therefore be repaired by the test fixture before being tested. Create roles before migrations and mirror the real bootstrap order.

### Frontend tests are partly brittle

Several frontend tests assert exact HTML strings and attribute ordering, for example `tests/integration/test_api.py:198–226`. Retain behavioral assertions, but avoid making harmless markup changes fail while browser-only failures such as blank Swagger remain undetected.

## README review

The README is useful but more confident than the implementation warrants in several places.

### 1. Request-volume claims are stale

`README.md:40–43` and ADR-0007 say roughly 40 requests daily. `docs/architecture.md:10–11,168–170` describes roughly 1,320 for UNIQLO alone.

### 2. “Every” IKEA item overstates coverage

`README.md:12` promises every offer and last-chance item. The adapter describes bounded sort-union discovery with an under-reported total, and skips explicitly non-online-sellable products.

### 3. Recovery guarantees need qualification

`README.md:95–97,191–193` does not distinguish handled exceptions from hard termination, nor normal retries from out-of-order replay.

### 4. The local email example is incomplete

`README.md:65` consumes `result.json`, but preceding commands never create it. The documented command fails on that missing file. Show redirection from `pricepulse run` or `process`, and explain how to obtain the product ID and outbox key.

### 5. Architecture and engineering notes repeat themselves

Keep the introduction, screenshots, features, quick start, one architecture overview, and explicit limitations. Link to detailed deployment, cost, and engineering evidence rather than repeating them.

### 6. Date operational measurements

Cost, catalog size, request counts, and cold-start measurements need an “as of” date and their workload assumptions.

## Verification and limitations

- Lint passed; 80 files passed formatting checks.
- 58 unit tests passed.
- `make lint test` attempted the full suite, but 46 integration tests had setup errors because PostgreSQL was unavailable on port 5433. Docker access was denied.
- Browser-reviewed the live listing, product page, and `/docs`.
- Ran isolated reproductions for alert percentages, malformed filters, incomplete IKEA responses, legacy tags, delivery retries, and watcher subjects.
- No deployment, infrastructure mutation, production watch submission, or exploit attempt.
- Database-dependent and cloud-execution consequences remain code-derived rather than runtime-reproduced.

## Open decisions

1. **Is this primarily a personal/portfolio project, or are real subscribers being supported?** That determines the appropriate backup, abuse-control, and delivery guarantees.
2. **Should a watch measure a drop since the previous scrape, since confirmation, or against the historical usual price?** The current UI promises a drop, but the longer-term reference is unspecified.
3. **Should mail-client “Unsubscribe” stop one product watch or all watch emails?** Currently a multi-watch digest’s header targets only its first watch.
4. **Should sold-out products remain in the default deals list, or remain accessible only through their product pages?**

## Recommended implementation order

1. Close the deployment trust gap.
2. Constrain deployment IAM.
3. Fix watch arithmetic.
4. Fix ingestion chronology, recovery, and publication validation.
5. Repair `/docs`.
6. Address mail delivery and abuse controls.
7. Update documentation to match the resulting guarantees.

## Resolution (2026-09-07)

Every finding above is resolved and deployed; the open decisions were answered as noted.

| Finding | Resolution |
| --- | --- |
| 1 | `deploy.yml` plan job requires `workflow_run.event == push`, same repository, `head_branch == main`; credentials configured after the build; `NEON_API_KEY` step-scoped |
| 2 | `pricepulse-dev-boundary` (bootstrap-owned, self-propagating, self-protecting) on every role; deploy role can only mint bounded roles; IAM simulation: attach-to-self / edit-boundary / unbound-create all denied |
| 3 | Drops are measured from the 90-day peak (`PrevObservation.peak_price`); list price never enters a drop (decision: peak, not previous price) |
| 4 | `RUN_LEASE_S` = function timeout; a live claim raises `RunInProgressError`, only `succeeded` is `skipped` |
| 5 | `ingestion_run.observed_at` (0007) defines chronology; summary v4; `upsert_products` never moves current state backwards |
| 6 | `IkeaSource.parse` rejects a page without `items`; `MIN_PRODUCTS` per source fails the run before publishing |
| 7 | CSP-safe `/docs` (external SRI-pinned bootstrap) |
| 8 | Colour count derives from per-SKU stock; sold-out colour disables "Buy this colour" |
| 9 | Non-finite Decimals → 422 |
| 10 | Per-recipient isolation, `sent/` delivery markers, failures reported to the alarms topic (at-least-once, duplicates bounded to one in-flight recipient) |
| 11 | Advisory-locked quotas: per email, per hashed client IP (`client_hash`, 0008), global |
| 12 | `prevent_destroy`; daily `backup` Lambda + `pricepulse restore-watches` (tested, RPO 24 h) |
| 13, 14 | Legacy `NEW_PRODUCT` tag excluded; watcher-only subject |
| Outbox | Commit-first with self-healing resend for pending duplicates |
| Engine / rotation / migration timeout | `connect_timeout=10`, engine dropped on failed wait, `--cli-read-timeout 330` |
| Test review | Role test mirrors bootstrap order; markup assertions attribute-order-agnostic; all listed gaps covered |
| README review | Coverage wording, request volumes, dated measurements, deduplicated sections, runnable local example |
| Open decisions | 1: personal deployment, hardened anyway; 2: drop from the 90-day peak; 3: header unsubscribes all watches of the address; 4: sold-out hidden by default (`include_sold_out=true`) |
