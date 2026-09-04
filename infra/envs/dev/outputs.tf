output "api_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "cluster_endpoint" {
  value = aws_rds_cluster.main.endpoint
}

output "cluster_arn" {
  value = aws_rds_cluster.main.arn
}

output "cluster_resource_id" {
  value = aws_rds_cluster.main.cluster_resource_id
}

output "master_secret_arn" {
  value = aws_rds_cluster.main.master_user_secret[0].secret_arn
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
  }
}
