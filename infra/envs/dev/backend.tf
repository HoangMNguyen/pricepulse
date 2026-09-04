terraform {
  backend "s3" {
    # Bucket name comes from infra/bootstrap output; pass at init time so the account id is not
    # hardcoded:  terraform -chdir=infra/envs/dev init -backend-config="bucket=pricepulse-tfstate-<acct>"
    key          = "dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
