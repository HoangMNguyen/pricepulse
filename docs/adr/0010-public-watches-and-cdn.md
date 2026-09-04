# ADR-0010: Public watches with double opt-in, and CloudFront in front of the API

## Context

The first version gated `POST /v1/watches` behind one shared API key, so only the owner could
create alerts; the dashboard had one fixed sort; every page view woke the database; there was no
domain, no TLS termination under our control, and no security headers. Turning the portfolio into a
small public site raised three questions: how strangers may subscribe without turning the sender
into a spam source, how to keep the database asleep under traffic, and how to do both under $5/month.

## Decision

- **Double opt-in, token links, no accounts.** A watch is created unconfirmed with a random
  `token` (`secrets.token_urlsafe(32)`); the API writes an `outbox/watch_confirm/*.json` object and
  the `mailer` Lambda sends the confirmation (S3 event → SES). Only confirmed watches reach
  `classify_alerts`; every digest sent to a watcher carries `/watches/unsubscribe/<token>` and a
  `List-Unsubscribe` header. Abuse limits: unique `(product, email)`, at most 5 unconfirmed watches
  per email, outbox objects expire after 7 days. Admin list/delete stay behind the API key.
- **Outbox instead of sending from the API.** The API Lambda never holds SES permissions and never
  waits on SES; a failed send leaves the object in place for a retry. It is the same
  S3-event pattern the ingestion pipeline already uses.
- **CloudFront as the read cache.** The API marks read pages `Cache-Control: public, max-age=300,
  s-maxage=86400`; CloudFront caches them for a day and `notify` invalidates `/*` after each run.
  Watch, confirm, health and run routes use `Managed-CachingDisabled`. A response headers policy
  adds HSTS, CSP (CDN scripts remain SRI-pinned), `X-Frame-Options`, `nosniff`, referrer policy.
- **Optional custom domain** through ACM (DNS validation) + Route 53 aliases; without a domain the
  distribution serves on `*.cloudfront.net`. All resources are `count`-guarded by `domain_name`.

## Consequences

- Strangers can subscribe safely; the owner's SES reputation is protected by opt-in and unsubscribe.
  SES production access is still required for unverified recipients (README).
- Visitors between runs never touch Neon: the database stays suspended and the free CU-hours are
  spent by the pipeline only. A cold visitor after an invalidation pays one origin request.
- Mail-scanner prefetch of a confirmation link may auto-confirm; accepted for a price tracker
  (no purchase or PII beyond the address).
- Cost: CloudFront and ACM are free at this traffic; a hosted zone is $0.50/month; WAF was rejected
  ($5+/month) — rate limiting is left to API Gateway's default throttling.
