# Runbook

All commands assume `aws login` done, region `us-east-1`, and `cd infra/envs/dev` for outputs.

```bash
CLUSTER_ARN=$(terraform output -raw cluster_arn); SECRET_ARN=$(terraform output -raw master_secret_arn)
sql() { aws rds-data execute-statement --resource-arn "$CLUSTER_ARN" --secret-arn "$SECRET_ARN" --database pricepulse --sql "$1" --format-records-as JSON --query formattedRecords --output text; }
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

Created automatically per run. To add one by hand: `sql "SELECT ensure_price_partition('2027-01-01T00:00:00Z')"`.
To drop old data: `sql "DROP TABLE price_observation_2025_01"`.

## SES

Sandbox: sender and every recipient must be verified. `aws sesv2 get-email-identity --email-identity you@example.com --query VerificationStatus`.
Re-send verification: `aws sesv2 create-email-identity --email-identity you@example.com` (idempotent).

## Alarms

`pricepulse-dev-alarms` (SNS) receives Lambda error alarms, the ACU alarm, and `on_failure`
invocation records from `process` (JSON with `requestContext.condition` and the error payload).

## Tear down

```bash
terraform -chdir=infra/envs/dev destroy
terraform -chdir=infra/bootstrap destroy   # empties nothing: delete state bucket objects first
```
