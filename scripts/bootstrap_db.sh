#!/usr/bin/env bash
# One-time DB bootstrap after `terraform apply`: creates the least-privilege app roles.
# Idempotent; run from the laptop with psql against the Neon migrator URL.
#
#   scripts/bootstrap_db.sh
#
# Roles:
#   app_migrator  the Neon project's default role (neon_superuser); owns every object; used by the
#                 migrate Lambda (alembic upgrade head)
#   app_rw        DML only; used by process + api Lambdas. DDL-ish needs (partitions, matview
#                 refresh, pruning) go through SECURITY DEFINER helpers created by the migrations.
#   app_ro        SELECT only; for humans / BI.
#
# app_rw/app_ro are created with SQL on purpose: roles created through Neon's console/API/Terraform
# become members of neon_superuser, which would defeat least privilege.
set -euo pipefail
cd "$(dirname "$0")/../infra/envs/dev"

URL=$(terraform output -raw migrator_database_url)
psql "$URL" -v ON_ERROR_STOP=1 \
  -v rw_pw="$(terraform output -raw app_rw_password)" \
  -v ro_pw="$(terraform output -raw app_ro_password)" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_trgm;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN CREATE ROLE app_rw LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_ro') THEN CREATE ROLE app_ro LOGIN; END IF;
END
$$;
ALTER ROLE app_rw PASSWORD :'rw_pw';
ALTER ROLE app_ro PASSWORD :'ro_pw';
GRANT CONNECT ON DATABASE pricepulse TO app_rw, app_ro;
GRANT USAGE ON SCHEMA public TO app_rw, app_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public GRANT SELECT ON TABLES TO app_ro;
SQL
echo "done. Next: aws lambda invoke --function-name pricepulse-dev-migrate ..."
