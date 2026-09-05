# Runbook

All commands run from the repo root and assume `aws login` done, region `us-east-1`, and
`NEON_API_KEY` exported. Terraform is always addressed with `-chdir=infra/envs/dev`.

```bash
sql() { psql "$(terraform -chdir=infra/envs/dev output -raw migrator_database_url)" -Atc "$1"; }
```


## Credentials & connections

Operator secrets live outside the repo in `~/.config/pricepulse/secrets.env` (mode 600):
`AWS_PROFILE`/`AWS_REGION`, `TF_STATE_BUCKET`, `NEON_API_KEY`, `NEON_ORG_ID`, `NEON_PROJECT_ID`,
`PORKBUN_API_KEY`, `PORKBUN_SECRET_KEY`. Load them before any command below:

```bash
set -a; . ~/.config/pricepulse/secrets.env; set +a
aws login --profile cci-deploy                   # browser device flow; sessions expire after hours
aws sts get-caller-identity --query Account      # 471112846501
curl -sS -H "Authorization: Bearer $NEON_API_KEY" https://console.neon.tech/api/v2/users/me | jq .email
```

GitHub Actions has the same three secrets it needs (`AWS_DEPLOY_ROLE_ARN`, `TF_STATE_BUCKET`,
`NEON_API_KEY`); rotate with `gh secret set`.

## DNS (Porkbun -> Route 53)

`pricepulse.hoangmnguyen.com` is a Route 53 zone (Terraform) delegated by four NS records at
Porkbun, created through the Porkbun API. The parent domain's other records are untouched.

```bash
auth="{\"apikey\":\"$PORKBUN_API_KEY\",\"secretapikey\":\"$PORKBUN_SECRET_KEY\"}"
curl -sS -X POST https://api.porkbun.com/api/json/v3/dns/retrieve/hoangmnguyen.com -H 'content-type: application/json' -d "$auth" | jq '.records[] | {id,name,type,content}'
# add one NS record (repeat per name server from `terraform -chdir=infra/envs/dev output name_servers`):
curl -sS -X POST https://api.porkbun.com/api/json/v3/dns/create/hoangmnguyen.com -H 'content-type: application/json' \
  -d "{\"apikey\":\"$PORKBUN_API_KEY\",\"secretapikey\":\"$PORKBUN_SECRET_KEY\",\"name\":\"pricepulse\",\"type\":\"NS\",\"content\":\"ns-297.awsdns-37.com\",\"ttl\":\"600\"}"
# delete by id: POST .../dns/delete/hoangmnguyen.com/<id>
```

If the zone is ever recreated its name servers change: update the four NS records. Delegation
check: `dig NS pricepulse.hoangmnguyen.com @1.1.1.1`.

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
terraform -chdir=infra/envs/dev taint random_password.api_key && terraform -chdir=infra/envs/dev apply
terraform -chdir=infra/envs/dev output -raw api_key
```

## Query the database

```bash
sql "SELECT version_num FROM alembic_version"
sql "SELECT source_id, count(*) FROM product GROUP BY 1"
sql "SELECT source, name, current_price, reference_price, discount_pct FROM product_price_summary WHERE is_current ORDER BY discount_pct DESC LIMIT 10"
sql "SELECT source, count(*) FILTER (WHERE is_current) AS current, count(*) AS total FROM product_price_summary GROUP BY 1"
```

## Partitions

Created automatically per run; partitions older than `RETENTION_MONTHS` (13) are dropped after each run
by `prune_price_partitions()`, which refuses `keep_months < 1`. By hand:
`sql "SELECT ensure_price_partition('2027-01-01T00:00:00Z')"`, `sql "SELECT prune_price_partitions(13)"`.

A run whose summary refresh or prune failed is marked `failed` (the DML transaction is already
committed); Lambda's automatic retry reclaims it and produces the same alerts. See "Re-run a failed key".

## Watches

```bash
sql "SELECT email, product_id, confirmed_at, confirmation_sent_at FROM watch ORDER BY created_at DESC LIMIT 20"
sql "DELETE FROM watch WHERE confirmed_at IS NULL AND confirmation_sent_at < now() - INTERVAL '30 days'"   # stale sign-ups
aws s3 ls s3://$(terraform -chdir=infra/envs/dev output -raw raw_bucket)/outbox/watch_confirm/ --recursive | tail   # queued confirmations (7-day expiry)
aws logs tail /aws/lambda/pricepulse-dev-mailer --since 1h --format short
```

Admin routes (`GET /v1/watches?email=`, `DELETE /v1/watches/{id}`) take `X-API-Key`; the key is
`terraform -chdir=infra/envs/dev output -raw api_key`. Confirmation and unsubscribe links show a page on
GET and act on POST; a one-click unsubscribe (RFC 8058) is a POST to `/watches/unsubscribe/<token>`.

A confirmation that SES rejected (sandbox, unverified recipient) stays in the outbox; re-send it
after fixing the cause:

```bash
aws lambda invoke --function-name pricepulse-dev-mailer --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"s3":{"object":{"key":"outbox/watch_confirm/<date>/<id>.json"}}}]}' /dev/stdout
```

## CDN

Pages are cached up to 24 h at CloudFront and invalidated by `notify` after every run. Force it:
`aws cloudfront create-invalidation --distribution-id $(terraform -chdir=infra/envs/dev output -raw cloudfront_distribution_id) --paths '/*'`.
A deploy invalidates `/*`; if a page still looks stale, run the same `aws cloudfront create-invalidation` manually.
Custom domain: `terraform -chdir=infra/envs/dev output name_servers` → registrar; certificate status
`aws acm list-certificates --query 'CertificateSummaryList[].[DomainName,Status]'`.
The response-headers policy carries the CSP: no `'unsafe-inline'`, so a template must never add
inline `<style>`/`<script>`/`on*=` — put it in `src/pricepulse/api/static` instead.

## SES

Sandbox: sender and every recipient must be verified. `aws sesv2 get-email-identity --email-identity you@example.com --query VerificationStatus`.
Re-send verification: `aws sesv2 create-email-identity --email-identity you@example.com` (idempotent).

## Alarms

`pricepulse-dev-alarms` (SNS) receives Lambda error alarms, `no-scrape-<source>` (no scrape in
24 h — the schedule or the scraper is broken), `low-products-<source>` (a run parsed fewer than the
source's `min_products` — the adapter is probably broken), and `on_failure` invocation records from
the asynchronously invoked functions (`scrape`, `process`, `notify`, `mailer`; JSON with
`requestContext.condition` and the error payload). Both metric alarms evaluate one
24-hour period, so a bad datapoint keeps them in `ALARM` for up to a day; skipped (duplicate)
runs emit no `ProductsSeen` datapoint on purpose. Sources, schedules and thresholds come from the
`sources` Terraform variable.

## Database

Neon console → project `pricepulse-dev` shows compute hours, storage and connections. Roles:
`app_migrator` (owner, used by `migrate`), `app_rw` (process/api), `app_ro` (humans). Rotate an app
password: `terraform -chdir=infra/envs/dev taint random_password.app_rw && terraform -chdir=infra/envs/dev apply && scripts/bootstrap_db.sh`.

## Terraform state lock

An interrupted `apply` (killed process, lost session) leaves the S3 lock in place:

```bash
aws s3 cp s3://$TF_STATE_BUCKET/dev/terraform.tfstate.tflock - | jq -r .ID   # confirm nothing is running first
terraform -chdir=infra/envs/dev force-unlock -force <id>
```

## Tear down

```bash
aws s3 rm "s3://$(terraform -chdir=infra/envs/dev output -raw raw_bucket)" --recursive   # the raw bucket has no force_destroy
terraform -chdir=infra/envs/dev destroy      # includes the Neon project
terraform -chdir=infra/bootstrap destroy     # separate state: delete the state bucket's objects first
```
