# One-time bootstrap: the S3 bucket that holds Terraform state for every environment, and the
# permissions boundary every pricepulse role carries. Both live outside the deploy role's reach.
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

# Ceiling for every role in the stack, the deploy role included (ADR-0006): PowerUserAccess
# semantics plus IAM limited to pricepulse-* roles/policies. A bounded role can only create or
# grant permissions to roles that carry this same boundary, and nothing bounded can edit the
# boundary, the deploy role or the OIDC provider, so no chain of pricepulse-* roles escapes it.
# Mirrors data.aws_iam_policy_document.github_iam in infra/envs/dev/iam_github.tf: keep the two
# in step. Bootstrap has no per-environment naming; the name is hardcoded and referenced by ARN
# from the dev environment.
locals {
  account_id   = data.aws_caller_identity.current.account_id
  boundary_arn = "arn:aws:iam::${local.account_id}:policy/pricepulse-dev-boundary"
}

data "aws_iam_policy_document" "boundary" {
  statement {
    sid         = "PowerUser"
    not_actions = ["iam:*", "organizations:*", "account:*"]
    resources   = ["*"]
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
  }
  statement {
    sid       = "ReadOidcProvider"
    actions   = ["iam:GetOpenIDConnectProvider"]
    resources = ["arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"]
  }
  statement {
    sid = "GrantOnlyUnderBoundary"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
      "iam:AttachRolePolicy",
      "iam:PutRolePermissionsBoundary",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/pricepulse-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_arn]
    }
  }
  statement {
    sid = "ManageProjectRoles"
    actions = [
      "iam:Get*",
      "iam:List*",
      "iam:Tag*",
      "iam:Untag*",
      "iam:PassRole",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/pricepulse-*",
      "arn:aws:iam::${local.account_id}:policy/pricepulse-*",
    ]
  }
  statement {
    sid       = "KeepBoundaries"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
  statement {
    sid         = "ReadOnlyOnOwnTrust"
    effect      = "Deny"
    not_actions = ["iam:Get*", "iam:List*"]
    resources = [
      local.boundary_arn,
      "arn:aws:iam::${local.account_id}:role/pricepulse-dev-github-deploy",
      "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com",
    ]
  }
  # checkov:skip=CKV_AWS_109: a boundary is a ceiling, not a grant; writes are scoped to pricepulse-* under this boundary (ADR-0006)
  # checkov:skip=CKV_AWS_111: as above
  # checkov:skip=CKV_AWS_356: CreateServiceLinkedRole, the NotAction ceiling and the DeleteRolePermissionsBoundary deny require "*"
  # The PowerUser NotAction statement trips the generic "allows X" checks; it is the same ceiling
  # the deploy role already carries via the AWS-managed PowerUserAccess, now enforced as a boundary.
  # checkov:skip=CKV_AWS_107: NotAction ceiling mirrors PowerUserAccess (ADR-0006)
  # checkov:skip=CKV_AWS_108: as above
  # checkov:skip=CKV_AWS_110: as above; iam:* is denied on the boundary, the deploy role and the OIDC provider
}

resource "aws_iam_policy" "boundary" {
  name   = "pricepulse-dev-boundary"
  policy = data.aws_iam_policy_document.boundary.json
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "permissions_boundary_arn" {
  value = aws_iam_policy.boundary.arn
}
