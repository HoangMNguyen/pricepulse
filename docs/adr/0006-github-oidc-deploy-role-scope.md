# ADR-0006: GitHub OIDC deploy role scope

## Context

CI must run `terraform apply`. The alternatives are long-lived IAM user keys stored as GitHub
secrets, or a role assumed through GitHub's OIDC provider. Terraform for this stack touches
~15 AWS services, so a hand-written least-privilege policy is large and brittle.

## Decision

`pricepulse-dev-github-deploy` is assumable only by `repo:HoangMNguyen/pricepulse:ref:refs/heads/main`
via OIDC (`aud = sts.amazonaws.com`). It carries the AWS-managed `PowerUserAccess` policy plus an
inline policy allowing `iam:*` only on `role/pricepulse-*` and `policy/pricepulse-*`, and
`iam:CreateServiceLinkedRole`. The `deploy` workflow runs in the `dev` GitHub environment so a
required reviewer can gate applies.

## Consequences

- No static AWS credentials exist anywhere.
- The role can create/modify any non-IAM resource in the account: acceptable for a personal
  account dedicated to this project; not acceptable for a shared account. Tightening to a
  resource-scoped policy is the obvious hardening step and is listed in the README.
