# A Python 3.14 arm64 Lambda with its own least-privilege role, log group, and optional VPC config.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "vpc" {
  count      = var.in_vpc ? 1 : 0
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "extra" {
  name   = "${var.name}-inline"
  role   = aws_iam_role.this.id
  policy = var.role_policy_json
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 14
  # checkov:skip=CKV_AWS_158: default CloudWatch encryption is sufficient; no KMS key to manage
  # checkov:skip=CKV_AWS_338: 14-day retention is a deliberate cost choice for a personal project
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  handler          = var.handler
  runtime          = "python3.14"
  architectures    = ["arm64"]
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = var.layer_arns
  memory_size      = var.memory_mb
  timeout          = var.timeout_s

  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = var.environment
  }

  dynamic "vpc_config" {
    for_each = var.in_vpc ? [1] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = var.security_group_ids
    }
  }

  tracing_config {
    mode = "PassThrough"
  }

  depends_on = [
    aws_cloudwatch_log_group.this,
    aws_iam_role_policy_attachment.basic,
    aws_iam_role_policy_attachment.vpc,
  ]

  # checkov:skip=CKV_AWS_50: X-Ray tracing adds cost without value for a daily batch job
  # checkov:skip=CKV_AWS_116: DLQ is replaced by an on_failure Lambda Destination to SNS
  # checkov:skip=CKV_AWS_173: env vars hold no secrets (API key excepted; rotated via terraform)
  # checkov:skip=CKV_AWS_272: code signing is out of scope for a single-developer project
  # checkov:skip=CKV_AWS_117: scrape/notify intentionally run outside the VPC (need internet)
  # checkov:skip=CKV_AWS_115: reserved concurrency is unavailable under the 10-execution account quota
}
