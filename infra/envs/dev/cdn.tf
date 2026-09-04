# CloudFront in front of the HTTP API: caches the read pages (the API sets s-maxage) so the
# database rarely wakes for visitors, terminates TLS for the custom domain, and adds security
# headers. Optional domain: without `domain_name` the distribution serves on *.cloudfront.net.

locals {
  has_domain = var.domain_name != null
  zone_id    = local.has_domain ? coalesce(var.hosted_zone_id, try(aws_route53_zone.main[0].zone_id, null)) : null
  site_url   = "https://${coalesce(var.domain_name, aws_cloudfront_distribution.main.domain_name)}"
}

resource "aws_route53_zone" "main" {
  count = local.has_domain && var.hosted_zone_id == null ? 1 : 0
  name  = var.domain_name
  # checkov:skip=CKV2_AWS_38: DNSSEC signing is not needed for a personal site
  # checkov:skip=CKV2_AWS_39: query logging costs more than the traffic is worth
}

resource "aws_acm_certificate" "site" {
  count                     = local.has_domain ? 1 : 0
  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}"]
  validation_method         = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = local.has_domain ? {
    for dvo in aws_acm_certificate.site[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}
  zone_id         = local.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "site" {
  count                   = local.has_domain ? 1 : 0
  certificate_arn         = aws_acm_certificate.site[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

resource "aws_cloudfront_cache_policy" "reads" {
  name        = "${local.name}-reads"
  comment     = "Cache read pages; the origin controls freshness through s-maxage"
  min_ttl     = 0
  default_ttl = 3600
  max_ttl     = 86400
  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
    query_strings_config {
      query_string_behavior = "all"
    }
    headers_config {
      header_behavior = "none"
    }
    cookies_config {
      cookie_behavior = "none"
    }
  }
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name = "${local.name}-security"
  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    content_security_policy {
      # 'unsafe-inline': the pages carry small inline scripts/styles; the CDN files are SRI-pinned.
      content_security_policy = join("; ", [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src 'self' https: data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
      ])
      override = true
    }
  }
}

resource "aws_cloudfront_distribution" "main" {
  # checkov:skip=CKV_AWS_86: access logging to S3 costs more than the traffic is worth
  # checkov:skip=CKV2_AWS_47: WAF is $5+/month, over the whole project budget
  # checkov:skip=CKV_AWS_68: WAF is $5+/month, over the whole project budget
  # checkov:skip=CKV2_AWS_32: a response headers policy IS attached to every behaviour
  # checkov:skip=CKV2_AWS_42: the custom certificate is attached whenever a domain is configured
  # checkov:skip=CKV_AWS_374: a public read-only site; no reason to geo-block
  # checkov:skip=CKV_AWS_305: the origin is an API, "/" is a route, not an object
  # checkov:skip=CKV_AWS_310: one origin; failover would need a second deployment
  enabled         = true
  is_ipv6_enabled = true
  comment         = "PricePulse"
  price_class     = "PriceClass_100"
  http_version    = "http2and3"
  aliases         = local.has_domain ? [var.domain_name, "www.${var.domain_name}"] : []

  origin {
    origin_id   = "api"
    domain_name = replace(aws_apigatewayv2_api.main.api_endpoint, "https://", "")
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "api"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.reads.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
  }

  dynamic "ordered_cache_behavior" {
    for_each = ["/v1/watches*", "/watches/*", "/health", "/v1/runs*"]
    content {
      path_pattern               = ordered_cache_behavior.value
      target_origin_id           = "api"
      allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods             = ["GET", "HEAD"]
      cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
      origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
      response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
      viewer_protocol_policy     = "redirect-to-https"
      compress                   = true
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = local.has_domain ? null : true
    acm_certificate_arn            = local.has_domain ? aws_acm_certificate_validation.site[0].certificate_arn : null
    ssl_support_method             = local.has_domain ? "sni-only" : null
    minimum_protocol_version       = local.has_domain ? "TLSv1.2_2021" : "TLSv1"
  }
}

resource "aws_route53_record" "site" {
  for_each = local.has_domain ? {
    apex_a    = { name = var.domain_name, type = "A" }
    apex_aaaa = { name = var.domain_name, type = "AAAA" }
    www_a     = { name = "www.${var.domain_name}", type = "A" }
    www_aaaa  = { name = "www.${var.domain_name}", type = "AAAA" }
  } : {}
  zone_id = local.zone_id
  name    = each.value.name
  type    = each.value.type
  alias {
    name                   = aws_cloudfront_distribution.main.domain_name
    zone_id                = aws_cloudfront_distribution.main.hosted_zone_id
    evaluate_target_health = false
  }
}
