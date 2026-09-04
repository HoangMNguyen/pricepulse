# Runbook

All commands assume `aws login` done, region `us-east-1`, `NEON_API_KEY` exported, and
`cd infra/envs/dev` for outputs.

```bash
sql() { psql "$(terraform output -raw migrator_database_url)" -Atc "$1"; }
```

## Re-run a failed key

```bash
sql "SELECT id, raw_object_key, status, error FROM ingestion_run WHERE status = 'failed' ORDER BY id DESC LIMIT 5"
aws lambda invoke --function-name pricepulse-dev-process --cli-binary-format raw-in-base64-out \
  --payload '{"raw_object_key":"raw/ikea/2026-09-04/130001-abcd1234.json.gz"}' /dev/stdout
```

A `succeeded` key is never reprocessed. To force it: `sql "UPDATE ingestion_run SET status='failed' WHERE raw_object_key='…'"` first.

## Re-run a scrape now

```bash
aws lambda invoke --function-name pricepulse-dev-scrape --cli-binary-format raw-in-base64-out --payload '{"source":"uniqlo"}' /dev/stdout
aws logs tail /aws/lambda/pricepulse-dev-process --since 10m --follow
```

## Rotate the API key

```bash
terraform taint random_password.api_key && terraform apply
terraform output -raw api_key
```

## Query the database

```bash
sql "SELECT version_num FROM alembic_version"
sql "SELECT source_id, count(*) FROM product GROUP BY 1"
sql "SELECT name, current_price, reference_price, discount_pct FROM product_price_summary ORDER BY discount_pct DESC LIMIT 10"
```

## Partitions

Created automatically per run; partitions older than `RETENTION_MONTHS` (13) are dropped after each run
by `prune_price_partitions()`. By hand: `sql "SELECT ensure_price_partition('2027-01-01T00:00:00Z')"`,
`sql "SELECT prune_price_partitions(13)"`.

## Watches

```bash
sql "SELECT email, product_id, confirmed_at, confirmation_sent_at FROM watch ORDER BY created_at DESC LIMIT 20"
sql "DELETE FROM watch WHERE confirmed_at IS NULL AND confirmation_sent_at < now() - INTERVAL '30 days'"   # stale sign-ups
aws s3 ls s3://$(terraform -chdir=infra/envs/dev output -raw raw_bucket)/outbox/watch_confirm/ --recursive | tail   # queued confirmations (7-day expiry)
aws logs tail /aws/lambda/pricepulse-dev-mailer --since 1h --format short
```

Admin routes (`GET /v1/watches?email=`, `DELETE /v1/watches/{id}`) take `X-API-Key`; the key is
`terraform output -raw api_key`.

## CDN

Pages are cached up to 24 h at CloudFront and invalidated by `notify` after every run. Force it:
`aws cloudfront create-invalidation --distribution-id $(terraform -chdir=infra/envs/dev output -raw cloudfront_distribution_id) --paths '/*'`.
Custom domain: `terraform output name_servers` → registrar; certificate status
`aws acm list-certificates --query 'CertificateSummaryList[].[DomainName,Status]'`.

## SES

Sandbox: sender and every recipient must be verified. `aws sesv2 get-email-identity --email-identity you@example.com --query VerificationStatus`.
Re-send verification: `aws sesv2 create-email-identity --email-identity you@example.com` (idempotent).

## Alarms

`pricepulse-dev-alarms` (SNS) receives Lambda error alarms, `no-scrape-<source>` (no scrape in
24 h — the schedule or the scraper is broken), `low-products-<source>` (a run parsed fewer than 100
products — the adapter is probably broken), and `on_failure` invocation records from `process`
(JSON with `requestContext.condition` and the error payload).

## Database

Neon console → project `pricepulse-dev` shows compute hours, storage and connections. Roles:
`app_migrator` (owner, used by `migrate`), `app_rw` (process/api), `app_ro` (humans). Rotate an app
password: `terraform taint random_password.app_rw && terraform apply && scripts/bootstrap_db.sh`.

## Tear down

```bash
terraform -chdir=infra/envs/dev destroy      # includes the Neon project
terraform -chdir=infra/bootstrap destroy   # empties nothing: delete state bucket objects first
```
