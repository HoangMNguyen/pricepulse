# A Python 3.14 arm64 Lambda with its own least-privilege role and log group.

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

  tracing_config {
    mode = "PassThrough"
  }

  depends_on = [
    aws_cloudwatch_log_group.this,
    aws_iam_role_policy_attachment.basic,
  ]

  # checkov:skip=CKV_AWS_50: X-Ray tracing adds cost without value for a daily batch job
  # checkov:skip=CKV_AWS_116: every asynchronously invoked function has an on_failure Destination to SNS; api and migrate are invoked synchronously
  # checkov:skip=CKV_AWS_173: env vars hold no secrets (API key excepted; rotated via terraform)
  # checkov:skip=CKV_AWS_272: code signing is out of scope for a single-developer project
  # checkov:skip=CKV_AWS_117: no VPC by design; the database is an external managed service (ADR-0009)
  # checkov:skip=CKV_AWS_115: reserved concurrency is unavailable under the 10-execution account quota
}
