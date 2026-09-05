output "api_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "migrator_database_url" {
  value     = replace(local.db_urls.app_migrator, "postgresql+psycopg://", "postgresql://")
  sensitive = true
}

output "app_rw_password" {
  value     = random_password.app_rw.result
  sensitive = true
}

output "app_ro_password" {
  value     = random_password.app_ro.result
  sensitive = true
}

output "github_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "api_key" {
  value     = random_password.api_key.result
  sensitive = true
}

output "function_names" {
  value = {
    scrape  = module.scrape.function_name
    process = module.process.function_name
    notify  = module.notify.function_name
    api     = module.api.function_name
    migrate = module.migrate.function_name
    mailer  = module.mailer.function_name
  }
}

output "site_url" {
  value = local.site_url
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.main.id
}

output "name_servers" {
  description = "Delegate the domain to these at the registrar (only when Terraform created the zone)."
  value       = try(aws_route53_zone.main[0].name_servers, null)
}
