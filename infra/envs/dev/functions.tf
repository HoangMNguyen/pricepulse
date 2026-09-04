# Five functions, none in a VPC: the database is Neon (public TLS endpoint, ADR-0009).
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
}

# --- scrape: internet-facing, writes raw payloads ---------------------------------------------

data "aws_iam_policy_document" "scrape" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
}

module "scrape" {
  source           = "../../modules/lambda_function"
  name             = "${local.name}-scrape"
  handler          = "pricepulse.lambda_handlers.scrape.handler"
  package_path     = var.app_package
  layer_arns       = [aws_lambda_layer_version.deps.arn]
  memory_mb        = 512
  timeout_s        = 300
  role_policy_json = data.aws_iam_policy_document.scrape.json
  environment      = merge(local.common_env, { RAW_BUCKET = aws_s3_bucket.raw.bucket })
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
  source           = "../../modules/lambda_function"
  name             = "${local.name}-process"
  handler          = "pricepulse.lambda_handlers.process.handler"
  package_path     = var.app_package
  layer_arns       = [aws_lambda_layer_version.deps.arn]
  memory_mb        = 1024
  timeout_s        = 600
  role_policy_json = data.aws_iam_policy_document.process.json
  environment = merge(local.common_env, {
    RAW_BUCKET             = aws_s3_bucket.raw.bucket
    DATABASE_URL_SSM       = aws_ssm_parameter.database_url["app_rw"].name
    ALERT_MIN_DISCOUNT_PCT = tostring(var.alert_min_discount_pct)
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
}

module "notify" {
  source           = "../../modules/lambda_function"
  name             = "${local.name}-notify"
  handler          = "pricepulse.lambda_handlers.notify.handler"
  package_path     = var.app_package
  layer_arns       = [aws_lambda_layer_version.deps.arn]
  memory_mb        = 256
  timeout_s        = 60
  role_policy_json = data.aws_iam_policy_document.notify.json
  environment = merge(local.common_env, {
    SES_SENDER                 = var.ses_sender
    ALERT_RECIPIENTS           = join(",", var.alert_recipients)
    PUBLIC_BASE_URL            = local.site_url
    CLOUDFRONT_DISTRIBUTION_ID = aws_cloudfront_distribution.main.id
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
}

module "mailer" {
  source           = "../../modules/lambda_function"
  name             = "${local.name}-mailer"
  handler          = "pricepulse.lambda_handlers.mailer.handler"
  package_path     = var.app_package
  layer_arns       = [aws_lambda_layer_version.deps.arn]
  memory_mb        = 256
  timeout_s        = 60
  role_policy_json = data.aws_iam_policy_document.mailer.json
  environment = merge(local.common_env, {
    SES_SENDER      = var.ses_sender
    RAW_BUCKET      = aws_s3_bucket.raw.bucket
    PUBLIC_BASE_URL = local.site_url
  })
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
  source           = "../../modules/lambda_function"
  name             = "${local.name}-api"
  handler          = "pricepulse.lambda_handlers.api.handler"
  package_path     = var.app_package
  layer_arns       = [aws_lambda_layer_version.deps.arn]
  memory_mb        = 1024
  timeout_s        = 29
  role_policy_json = data.aws_iam_policy_document.api.json
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
  source           = "../../modules/lambda_function"
  name             = "${local.name}-migrate"
  handler          = "pricepulse.lambda_handlers.migrate.handler"
  package_path     = var.app_package
  layer_arns       = [aws_lambda_layer_version.deps.arn]
  memory_mb        = 512
  timeout_s        = 300
  role_policy_json = data.aws_iam_policy_document.migrate.json
  environment = merge(local.common_env, {
    DATABASE_URL_SSM = aws_ssm_parameter.database_url["app_migrator"].name
  })
}
