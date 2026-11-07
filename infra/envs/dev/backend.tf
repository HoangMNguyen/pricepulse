terraform {
  backend "s3" {
    # The state bucket is owned by the account foundations stack (HoangMNguyen/hoangmnguyen.com,
    # infra/account). Pass the bucket at init time so the account id is not hardcoded:
    #   terraform -chdir=infra/envs/dev init -backend-config="bucket=pricepulse-tfstate-<acct>"
    key          = "dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
