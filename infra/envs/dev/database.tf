# Neon Free: serverless PostgreSQL 16 that suspends after 5 idle minutes and resumes in ≈ 1 s.
# The project's default role (app_migrator) owns the schema; least-privilege app roles are
# created with SQL by scripts/bootstrap_db.sh. Connection URLs (with credentials) live in SSM
# SecureString parameters, one per role, read by each Lambda at cold start. See ADR-0009.

resource "neon_project" "main" {
  name                      = local.name
  org_id                    = var.neon_org_id
  region_id                 = "aws-us-east-1"
  pg_version                = 16
  history_retention_seconds = 21600 # Free plan maximum (6 h)

  branch {
    name          = "main"
    database_name = "pricepulse"
    role_name     = "app_migrator"
  }

  default_endpoint_settings {
    autoscaling_limit_min_cu = 0.25
    autoscaling_limit_max_cu = 0.25
    # suspend_timeout_seconds is not settable on the Free plan; its default is the 300 s we want.
  }
}

resource "random_password" "app_rw" {
  length  = 32
  special = false
}

resource "random_password" "app_ro" {
  length  = 32
  special = false
}

locals {
  db_query = "sslmode=require&channel_binding=require"
  db_urls = {
    app_migrator = "postgresql+psycopg://${neon_project.main.database_user}:${neon_project.main.database_password}@${neon_project.main.database_host}/${neon_project.main.database_name}?${local.db_query}"
    app_rw       = "postgresql+psycopg://app_rw:${random_password.app_rw.result}@${neon_project.main.database_host}/${neon_project.main.database_name}?${local.db_query}"
    app_ro       = "postgresql+psycopg://app_ro:${random_password.app_ro.result}@${neon_project.main.database_host}/${neon_project.main.database_name}?${local.db_query}"
  }
}

resource "aws_ssm_parameter" "database_url" {
  for_each = local.db_urls
  name     = "/pricepulse/dev/database_url/${each.key}"
  type     = "SecureString"
  value    = each.value
  # checkov:skip=CKV_AWS_337: default aws/ssm key is sufficient; no customer KMS key to manage
}

data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}
