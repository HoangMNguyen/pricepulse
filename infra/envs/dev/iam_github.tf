# GitHub Actions deploys through OIDC: no long-lived AWS keys anywhere. Scope tradeoff in ADR-0006.

locals {
  github_owner     = split("/", var.github_repo)[0]
  github_repo_name = split("/", var.github_repo)[1]
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates GitHub's certificate chain via its own trust store; thumbprints are legacy.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # GitHub's subject now embeds owner/repo IDs: `repo:<owner>@<id>/<repo>@<id>:<scope>`.
    # Jobs targeting a GitHub environment present `environment:dev`; plain jobs present the ref.
    # Wildcards cover only the numeric IDs; owner and repo names stay pinned.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${local.github_owner}@*/${local.github_repo_name}@*:ref:refs/heads/main",
        "repo:${local.github_owner}@*/${local.github_repo_name}@*:environment:dev",
        "repo:${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_repo}:environment:dev",
      ]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

resource "aws_iam_role_policy_attachment" "github_poweruser" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "github_iam" {
  statement {
    sid     = "ManageProjectRoles"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${local.account_id}:role/pricepulse-*",
      "arn:aws:iam::${local.account_id}:policy/pricepulse-*",
    ]
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
  }
  statement {
    sid       = "ReadOidcProvider"
    actions   = ["iam:GetOpenIDConnectProvider"]
    resources = [aws_iam_openid_connect_provider.github.arn]
  }
  # checkov:skip=CKV_AWS_109: iam:* is constrained to pricepulse-* roles/policies (ADR-0006)
  # checkov:skip=CKV_AWS_111: as above
  # checkov:skip=CKV_AWS_356: CreateServiceLinkedRole requires "*"
}

resource "aws_iam_role_policy" "github_iam" {
  name   = "${local.name}-github-iam"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_iam.json
}
