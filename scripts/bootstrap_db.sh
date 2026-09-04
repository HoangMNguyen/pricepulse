#!/usr/bin/env bash
# One-time DB bootstrap after `terraform apply`: creates the IAM-authenticated app roles and the
# privilege model. Runs over the RDS Data API with the master secret; no network path needed.
#
#   scripts/bootstrap_db.sh            # reads terraform outputs from infra/envs/dev
#
# Roles:
#   app_migrator  owns every object; used by the migrate Lambda (alembic upgrade head)
#   app_rw        DML only; used by process + api Lambdas. DDL-ish needs (partitions, matview
#                 refresh) go through SECURITY DEFINER helpers created by the migrations.
#   app_ro        SELECT only; for humans / BI via the Data API.
set -euo pipefail

cd "$(dirname "$0")/../infra/envs/dev"
CLUSTER_ARN=$(terraform output -raw cluster_arn)
SECRET_ARN=$(terraform output -raw master_secret_arn)
DB=pricepulse

run() {
  echo ">> $1"
  aws rds-data execute-statement --resource-arn "$CLUSTER_ARN" --secret-arn "$SECRET_ARN" \
    --database "$DB" --sql "$1" --output text --query 'numberOfRecordsUpdated' >/dev/null
}

run "CREATE EXTENSION IF NOT EXISTS pg_trgm"
for role in app_migrator app_rw app_ro; do
  run "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$role') THEN CREATE ROLE $role LOGIN; END IF; END \$\$"
  run "GRANT rds_iam TO $role"
done
# The master user is not a superuser: ALTER DEFAULT PRIVILEGES FOR ROLE requires membership.
run "GRANT app_migrator TO pricepulse_admin"
run "GRANT CONNECT ON DATABASE $DB TO app_migrator, app_rw, app_ro"
run "GRANT ALL ON SCHEMA public TO app_migrator"
run "GRANT USAGE ON SCHEMA public TO app_rw, app_ro"
run "ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw"
run "ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_rw"
run "ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public GRANT SELECT ON TABLES TO app_ro"
# Function EXECUTE is granted explicitly per helper inside the migrations (PUBLIC is revoked).
echo "done. Next: aws lambda invoke --function-name pricepulse-dev-migrate ..."
