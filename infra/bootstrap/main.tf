# One-time bootstrap: the S3 bucket that holds Terraform state for every environment.
# Uses local state on purpose (chicken-and-egg). Run once:
#   terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { Project = "pricepulse", ManagedBy = "terraform", Env = "shared" }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "tfstate" {
  bucket = "pricepulse-tfstate-${data.aws_caller_identity.current.account_id}"
  # checkov:skip=CKV_AWS_144: cross-region replication is unnecessary for a personal project
  # checkov:skip=CKV_AWS_18: access logging bucket would cost more than the state it logs
  # checkov:skip=CKV2_AWS_62: no event notifications needed for state
  # checkov:skip=CKV_AWS_145: SSE-S3 is sufficient; no KMS key to manage
  # checkov:skip=CKV2_AWS_61: state must be retained; no lifecycle expiry
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}
