# One-time bootstrap: the permissions boundary every pricepulse role carries. It lives outside the
# deploy role's reach, so it is applied locally with an admin login:
#   terraform -chdir=infra/bootstrap init -backend-config="bucket=pricepulse-tfstate-<acct>"
#   terraform -chdir=infra/bootstrap apply
#
# The Terraform state bucket (pricepulse-tfstate-<acct>) and the GitHub OIDC provider are shared
# with other projects in the account and are owned by the account foundations stack
# (HoangMNguyen/hoangmnguyen.com, infra/account). This stack used to create the bucket and keep
# local state for that chicken-and-egg reason; with the bucket owned elsewhere, its state now lives
# in the bucket under bootstrap/, a key the deploy role cannot read or write.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { Project = "pricepulse", ManagedBy = "terraform", Env = "shared" }
  }
}

data "aws_caller_identity" "current" {}

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

output "permissions_boundary_arn" {
  value = aws_iam_policy.boundary.arn
}
