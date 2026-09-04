# HTTP API (v2) in front of the FastAPI Lambda. $default route + stage with auto-deploy.

resource "aws_apigatewayv2_api" "main" {
  name          = local.name
  protocol_type = "HTTP"
  description   = "PricePulse API + dashboard"
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
  # checkov:skip=CKV_AWS_309: auth is an application-level API key on write routes; reads are public
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = 14
  # checkov:skip=CKV_AWS_158: default encryption is sufficient
  # checkov:skip=CKV_AWS_338: 14-day retention is a deliberate cost choice
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId", ip = "$context.identity.sourceIp",
      method    = "$context.httpMethod", path = "$context.path", status = "$context.status",
      latency   = "$context.responseLatency", integrationError = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "apigw_invoke_api" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}
