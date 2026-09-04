# Five functions. VPC placement: only those that talk to the DB. Destinations carry the
# processor's result to the notifier so no network path out of the VPC is needed.

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
  db_env = {
    DB_HOST     = aws_rds_cluster.main.endpoint
    DB_NAME     = aws_rds_cluster.main.database_name
    DB_IAM_AUTH = "true"
  }
  vpc = {
    subnet_ids         = [for s in aws_subnet.private : s.id]
    security_group_ids = [aws_security_group.lambda.id]
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

# --- process: in VPC, S3 -> Postgres, returns alerts -------------------------------------------
# No reserved concurrency: new accounts have a 10-execution quota, and reserving any of it is
# rejected. Runs are serialized in practice by the staggered schedule and by `claim_run`.

data "aws_iam_policy_document" "process" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
  statement {
    actions   = ["rds-db:connect"]
    resources = ["${local.db_user_arn_prefix}/app_rw"]
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
  source             = "../../modules/lambda_function"
  name               = "${local.name}-process"
  handler            = "pricepulse.lambda_handlers.process.handler"
  package_path       = var.app_package
  layer_arns         = [aws_lambda_layer_version.deps.arn]
  memory_mb          = 1024
  timeout_s          = 600
  in_vpc             = true
  subnet_ids         = local.vpc.subnet_ids
  security_group_ids = local.vpc.security_group_ids
  role_policy_json   = data.aws_iam_policy_document.process.json
  environment = merge(local.common_env, local.db_env, {
    RAW_BUCKET             = aws_s3_bucket.raw.bucket
    DB_USER                = "app_rw"
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
    SES_SENDER       = var.ses_sender
    ALERT_RECIPIENTS = join(",", var.alert_recipients)
  })
}

# --- api: in VPC, FastAPI via Mangum -----------------------------------------------------------

data "aws_iam_policy_document" "api" {
  statement {
    actions   = ["rds-db:connect"]
    resources = ["${local.db_user_arn_prefix}/app_rw"]
  }
}

module "api" {
  source             = "../../modules/lambda_function"
  name               = "${local.name}-api"
  handler            = "pricepulse.lambda_handlers.api.handler"
  package_path       = var.app_package
  layer_arns         = [aws_lambda_layer_version.deps.arn]
  memory_mb          = 1024
  timeout_s          = 29
  in_vpc             = true
  subnet_ids         = local.vpc.subnet_ids
  security_group_ids = local.vpc.security_group_ids
  role_policy_json   = data.aws_iam_policy_document.api.json
  environment = merge(local.common_env, local.db_env, {
    DB_USER = "app_rw"
    API_KEY = random_password.api_key.result
  })
}

# --- migrate: in VPC, alembic upgrade head as app_migrator ------------------------------------

data "aws_iam_policy_document" "migrate" {
  statement {
    actions   = ["rds-db:connect"]
    resources = ["${local.db_user_arn_prefix}/app_migrator"]
  }
}

module "migrate" {
  source             = "../../modules/lambda_function"
  name               = "${local.name}-migrate"
  handler            = "pricepulse.lambda_handlers.migrate.handler"
  package_path       = var.app_package
  layer_arns         = [aws_lambda_layer_version.deps.arn]
  memory_mb          = 512
  timeout_s          = 300
  in_vpc             = true
  subnet_ids         = local.vpc.subnet_ids
  security_group_ids = local.vpc.security_group_ids
  role_policy_json   = data.aws_iam_policy_document.migrate.json
  environment        = merge(local.common_env, local.db_env, { DB_USER = "app_migrator" })
}
