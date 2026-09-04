# SES identities (sandbox: sender AND every recipient must be verified) and the alarms topic.

resource "aws_sesv2_email_identity" "sender" {
  email_identity = var.ses_sender
}

resource "aws_sesv2_email_identity" "recipients" {
  for_each       = toset([for r in var.alert_recipients : r if r != var.ses_sender])
  email_identity = each.value
}

resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
  # checkov:skip=CKV_AWS_26: topic carries alarm text and Lambda failure records only; no secrets
}

resource "aws_sns_topic_subscription" "alarms_email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alert_recipients[0]
}

data "aws_iam_policy_document" "alarms_topic" {
  statement {
    sid     = "AllowCloudWatchAndBudgetsPublish"
    actions = ["sns:Publish"]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
    }
    resources = [aws_sns_topic.alarms.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.alarms_topic.json
}
