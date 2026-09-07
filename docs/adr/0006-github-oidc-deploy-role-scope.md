# ADR-0006: GitHub OIDC deploy role scope

## Context

CI must run `terraform apply`. The alternatives are long-lived IAM user keys stored as GitHub
secrets, or a role assumed through GitHub's OIDC provider. Terraform for this stack touches
~15 AWS services, so a hand-written least-privilege policy is large and brittle.

## Decision

`pricepulse-dev-github-deploy` is assumable only via OIDC (`aud = sts.amazonaws.com`) by subjects
`repo:HoangMNguyen@<id>/pricepulse@<id>:environment:dev` or `...:ref:refs/heads/main` (GitHub embeds
owner/repo IDs in the subject; the trust policy wildcards only those IDs). It carries the AWS-managed `PowerUserAccess` policy plus an
inline policy allowing `iam:*` only on `role/pricepulse-*` and `policy/pricepulse-*`, and
`iam:CreateServiceLinkedRole`. The `deploy` workflow runs in the `dev` GitHub environment so a
required reviewer can gate applies.

## Consequences

- No static AWS credentials exist anywhere.
- The role can create/modify any non-IAM resource in the account: acceptable for a personal
  account dedicated to this project; not acceptable for a shared account. Tightening to a
  resource-scoped policy is the obvious hardening step and is listed in the README.

## 2026-09-07: permissions boundary and a trusted trigger

The original scope let the deploy role create a `pricepulse-*` role with any policy, then assume
it: the name prefix was a filter, not a ceiling. And the `deploy` workflow trusted any successful
`ci` run whose head branch was named `main`, including one from a fork pull request, before
acquiring AWS credentials.

### Decision

- `infra/bootstrap` (human-applied, local state) now also owns a managed policy
  `pricepulse-dev-boundary`, the ceiling for every role in the stack: `PowerUserAccess`
  semantics, `iam:CreateServiceLinkedRole`, read of the GitHub OIDC provider, and IAM on
  `pricepulse-*` roles/policies in the same shape as the deploy role's inline policy below.
- The boundary is self-propagating and self-protecting. `iam:CreateRole`, `PutRolePolicy`,
  `AttachRolePolicy` and `PutRolePermissionsBoundary` are allowed only when the request names the
  boundary itself (`iam:PermissionsBoundary` condition), so a bounded role can only mint bounded
  roles; `iam:DeleteRolePermissionsBoundary` is denied everywhere; every non-read IAM action on
  the boundary policy, the deploy role and the OIDC provider is denied. Without this a bounded
  role could create a `pricepulse-*` role that rewrites the boundary (it matches the prefix) or an
  unbounded one with `AdministratorAccess`.
- Every role Terraform creates in `infra/envs/dev` — the six Lambda roles, the scheduler role and
  the deploy role itself — carries that boundary. The `lambda_function` module requires it.
- The deploy role's inline policy is the same document minus the `PowerUser` ceiling: the
  conditioned grant statement, the enumerated management actions instead of `iam:*`, and the two
  denies. The two documents must stay mirrored; a permission missing from either is denied.
- `deploy.yml` runs only when `ci` succeeded for a `push` to `main` of this repository
  (`workflow_run.event`, `head_repository.full_name`, `head_branch`), configures AWS credentials
  after the build, and exposes `NEON_API_KEY` only to the steps that call Neon or Terraform.

### Bootstrap order

The deploy role cannot attach its own boundary, change its own inline policy, or create the OIDC
provider: those are exactly the actions the denies cover. The order is therefore:

1. `terraform -chdir=infra/bootstrap apply` as a human: state bucket and boundary.
2. `terraform -chdir=infra/envs/dev apply` as a human: OIDC provider, deploy role with boundary
   and inline policy, everything else.
3. CI from then on. Any later change to the deploy role, its inline policy, its tags, the OIDC
   provider or the boundary fails in CI with `AccessDenied` and is applied by a human again.

### Consequences

- Creating a `pricepulse-*` role with a policy broader than the boundary no longer yields more
  than the boundary grants, so the role-name prefix is now a real limit.
- The deploy role can still read and write every non-IAM resource in the account; the "known gap"
  in the README stands for that part.
- A fork pull request can no longer reach the plan job, and a compromised build script no longer
  runs with AWS credentials in the environment.
