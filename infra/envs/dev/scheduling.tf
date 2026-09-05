# One daily scrape per retailer, staggered by 10 minutes.

resource "aws_scheduler_schedule_group" "main" {
  name = local.name
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [module.scrape.function_arn]
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.name}-scheduler-invoke"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "scrape" {
  # checkov:skip=CKV_AWS_297: schedule input is {"source": ...}; a CMK adds $1/mo for nothing
  for_each                     = var.sources
  name                         = "${local.name}-scrape-${each.key}"
  group_name                   = aws_scheduler_schedule_group.main.name
  schedule_expression          = each.value.schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.scrape.function_arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ source = each.key })
    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}
