# Error alarms on the batch functions, missing/short scrape alarms, and a hard monthly budget.

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = { scrape = module.scrape.function_name, process = module.process.function_name }
  alarm_name          = "${each.value}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# Silence is a failure: each retailer must scrape once a day and return a plausible catalog.
resource "aws_cloudwatch_metric_alarm" "no_scrape" {
  for_each            = var.sources
  alarm_name          = "${local.name}-no-scrape-${each.key}"
  namespace           = "PricePulse"
  metric_name         = "ScrapeRuns"
  dimensions          = { source = each.key, service = "pricepulse" }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "low_products" {
  for_each            = var.sources
  alarm_name          = "${local.name}-low-products-${each.key}"
  namespace           = "PricePulse"
  metric_name         = "ProductsSeen"
  dimensions          = { source = each.key, service = "pricepulse" }
  statistic           = "Minimum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = each.value.min_products
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# No budget here: AWS budgets are account-wide whatever their name, so the account's single cost
# budget lives in the account foundations stack (HoangMNguyen/hoangmnguyen.com, infra/account).
