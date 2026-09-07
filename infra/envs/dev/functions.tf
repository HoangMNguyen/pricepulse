# Six functions, none in a VPC: the database is Neon (public TLS endpoint, ADR-0009).
# Destinations carry the processor's result to the notifier.

resource "aws_lambda_layer_version" "deps" {
  layer_name               = "${local.name}-deps"
  filename                 = var.layer_package
  source_code_hash         = filebase64sha256(var.layer_package)
  compatible_runtimes      = ["python3.14"]
  compatible_architectures = ["arm64"]
}

resource "random_password" "api_key" {
  length  = 32
  special = false
}

locals {
  common_env = {
    PRICEPULSE_ENV          = "dev"
    USER_AGENT              = var.user_agent
    POWERTOOLS_SERVICE_NAME = "pricepulse"
    POWERTOOLS_LOG_LEVEL    = "INFO"
  }
  # The run lease must equal the Lambda timeout: a lease longer than the timeout leaves a
  # killed run unclaimable by its retry, a shorter one lets the retry double-process.
  process_timeout_s = 600
}

# --- scrape: internet-facing, writes raw payloads ---------------------------------------------

data "aws_iam_policy_document" "scrape" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
  }
}

module "scrape" {
  source               = "../../modules/lambda_function"
  name                 = "${local.name}-scrape"
  handler              = "pricepulse.lambda_handlers.scrape.handler"
  package_path         = var.app_package
  layer_arns           = [aws_lambda_layer_version.deps.arn]
  memory_mb            = 512
  timeout_s            = 900 # UNIQLO: ~1,300 per-SKU stock calls over 3 workers ≈ 6 min
  role_policy_json     = data.aws_iam_policy_document.scrape.json
  permissions_boundary = data.aws_iam_policy.boundary.arn
  environment          = merge(local.common_env, { RAW_BUCKET = aws_s3_bucket.raw.bucket })
}

# --- process: S3 -> Postgres, returns alerts ---------------------------------------------------
# No reserved concurrency: new accounts have a 10-execution quota, and reserving any of it is
# rejected. Runs are serialized in practice by the staggered schedule and by `claim_run`.

data "aws_iam_policy_document" "process" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.database_url["app_rw"].arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [module.notify.function_arn]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
  }
}

module "process" {
  source               = "../../modules/lambda_function"
  name                 = "${local.name}-process"
  handler              = "pricepulse.lambda_handlers.process.handler"
  package_path         = var.app_package
  layer_arns           = [aws_lambda_layer_version.deps.arn]
  memory_mb            = 1024
  timeout_s            = local.process_timeout_s
  role_policy_json     = data.aws_iam_policy_document.process.json
  permissions_boundary = data.aws_iam_policy.boundary.arn
  environment = merge(local.common_env, {
    RAW_BUCKET             = aws_s3_bucket.raw.bucket
    DATABASE_URL_SSM       = aws_ssm_parameter.database_url["app_rw"].name
    ALERT_MIN_DISCOUNT_PCT = tostring(var.alert_min_discount_pct)
    RUN_LEASE_S            = tostring(local.process_timeout_s)
    # The same per-source floors as the low-products alarm; process fails a short payload
    # before publishing it.
    MIN_PRODUCTS = jsonencode({ for k, s in var.sources : k => s.min_products })
  })
}

resource "aws_lambda_function_event_invoke_config" "process" {
  function_name          = module.process.function_name
  maximum_retry_attempts = 1
  destination_config {
    on_success {
      destination = module.notify.function_arn
    }
    on_failure {
      destination = aws_sns_topic.alarms.arn
    }
  }
}

# --- notify: internet-facing (SES), fed by Lambda Destinations -------------------------------

data "aws_iam_policy_document" "notify" {
  statement {
    actions = ["ses:SendEmail"]
    resources = concat(
      [aws_sesv2_email_identity.sender.arn],
      [for i in aws_sesv2_email_identity.recipients : i.arn],
    )
  }
  statement {
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.main.arn]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
  }
  statement {
    # Delivery markers: one empty object per (run, recipient), written after SES accepts.
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/sent/*"]
  }
}

module "notify" {
  source               = "../../modules/lambda_function"
  name                 = "${local.name}-notify"
  handler              = "pricepulse.lambda_handlers.notify.handler"
  package_path         = var.app_package
  layer_arns           = [aws_lambda_layer_version.deps.arn]
  memory_mb            = 256
  timeout_s            = 60
  role_policy_json     = data.aws_iam_policy_document.notify.json
  permissions_boundary = data.aws_iam_policy.boundary.arn
  environment = merge(local.common_env, {
    SES_SENDER                 = var.ses_sender
    ALERT_RECIPIENTS           = join(",", var.alert_recipients)
    PUBLIC_BASE_URL            = local.site_url
    CLOUDFRONT_DISTRIBUTION_ID = aws_cloudfront_distribution.main.id
    ALARMS_TOPIC_ARN           = aws_sns_topic.alarms.arn # undelivered digests, never retried
    RAW_BUCKET                 = aws_s3_bucket.raw.bucket # sent/ delivery markers
  })
}

# --- mailer: transactional mail (watch confirmations) from outbox/ objects -----------------------

data "aws_iam_policy_document" "mailer" {
  statement {
    actions   = ["ses:SendEmail"]
    resources = [aws_sesv2_email_identity.sender.arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw.arn}/outbox/*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
  }
}

module "mailer" {
  source               = "../../modules/lambda_function"
  name                 = "${local.name}-mailer"
  handler              = "pricepulse.lambda_handlers.mailer.handler"
  package_path         = var.app_package
  layer_arns           = [aws_lambda_layer_version.deps.arn]
  memory_mb            = 256
  timeout_s            = 60
  role_policy_json     = data.aws_iam_policy_document.mailer.json
  permissions_boundary = data.aws_iam_policy.boundary.arn
  environment = merge(local.common_env, {
    SES_SENDER      = var.ses_sender
    RAW_BUCKET      = aws_s3_bucket.raw.bucket
    PUBLIC_BASE_URL = local.site_url
  })
}

# Every asynchronously invoked function reports failures to the alarms topic. process has its
# own config above (it also chains on_success to notify); api and migrate are invoked synchronously.
resource "aws_lambda_function_event_invoke_config" "async" {
  for_each = {
    scrape = module.scrape.function_name
    notify = module.notify.function_name
    mailer = module.mailer.function_name
    backup = module.backup.function_name
  }
  function_name          = each.value
  maximum_retry_attempts = 1
  destination_config {
    on_failure {
      destination = aws_sns_topic.alarms.arn
    }
  }
}

# --- api: FastAPI via Mangum -------------------------------------------------------------------

data "aws_iam_policy_document" "api" {
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.database_url["app_rw"].arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/outbox/*"]
  }
}

module "api" {
  source               = "../../modules/lambda_function"
  name                 = "${local.name}-api"
  handler              = "pricepulse.lambda_handlers.api.handler"
  package_path         = var.app_package
  layer_arns           = [aws_lambda_layer_version.deps.arn]
  memory_mb            = 1024
  timeout_s            = 29
  role_policy_json     = data.aws_iam_policy_document.api.json
  permissions_boundary = data.aws_iam_policy.boundary.arn
  environment = merge(local.common_env, {
    DATABASE_URL_SSM  = aws_ssm_parameter.database_url["app_rw"].name
    DB_CONNECT_WAIT_S = "20"
    API_KEY           = random_password.api_key.result
    RAW_BUCKET        = aws_s3_bucket.raw.bucket
  })
}

# --- migrate: alembic upgrade head as app_migrator ---------------------------------------------

data "aws_iam_policy_document" "migrate" {
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.database_url["app_migrator"].arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
}

module "migrate" {
  source               = "../../modules/lambda_function"
  name                 = "${local.name}-migrate"
  handler              = "pricepulse.lambda_handlers.migrate.handler"
  package_path         = var.app_package
  layer_arns           = [aws_lambda_layer_version.deps.arn]
  memory_mb            = 512
  timeout_s            = 300
  role_policy_json     = data.aws_iam_policy_document.migrate.json
  permissions_boundary = data.aws_iam_policy.boundary.arn
  environment = merge(local.common_env, {
    DATABASE_URL_SSM = aws_ssm_parameter.database_url["app_migrator"].name
  })
}

# --- backup: daily watch dump to backup/ (the one table raw payloads cannot rebuild) ----------

data "aws_iam_policy_document" "backup" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/backup/*"]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.database_url["app_rw"].arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
  statement {
    actions   = ["sns:Publish"] # on_failure destination
    resources = [aws_sns_topic.alarms.arn]
  }
}

module "backup" {
  source               = "../../modules/lambda_function"
  name                 = "${local.name}-backup"
  handler              = "pricepulse.lambda_handlers.backup.handler"
  package_path         = var.app_package
  layer_arns           = [aws_lambda_layer_version.deps.arn]
  memory_mb            = 256
  timeout_s            = 60
  role_policy_json     = data.aws_iam_policy_document.backup.json
  permissions_boundary = data.aws_iam_policy.boundary.arn
  environment = merge(local.common_env, {
    RAW_BUCKET       = aws_s3_bucket.raw.bucket
    DATABASE_URL_SSM = aws_ssm_parameter.database_url["app_rw"].name
  })
}
