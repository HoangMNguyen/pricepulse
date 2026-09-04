# ADR-0003: Private-only VPC (no NAT) and Lambda Destinations for egress

> **Superseded by [ADR-0009](0009-neon-instead-of-aurora.md)** — the database moved to Neon; the VPC and Aurora were removed. Kept for the record.

## Context

Aurora must live in a VPC. A Lambda inside a VPC has no internet access unless the VPC has a NAT
gateway (~$32/month + data), which alone would exceed the whole project budget by 10×. Yet the
pipeline must fetch from retailers and send email.

## Decision

Split functions by network need:

| Function | In VPC | Needs internet | Needs DB |
| --- | --- | --- | --- |
| scrape | no | yes (retailers) | no |
| process | yes | no | yes |
| notify | no | yes (SES) | no |
| api | yes | no | yes |
| migrate | yes | no | yes |

The VPC has two private subnets, one route table with no default route, and a **gateway** S3
endpoint (free) so `process` can read raw payloads. `process` hands its result to `notify`
through a Lambda **Destination** (`on_success`), which is delivered by the Lambda service itself
and therefore needs no network path out of the VPC. Failures go to an SNS topic (`on_failure`).

Security groups: Lambda SG egress only to the DB SG on 5432 and to the S3 prefix list on 443;
DB SG ingress only from the Lambda SG.

## Consequences

- $0 network cost; no public IPs anywhere.
- Lambdas inside the VPC cannot call any other AWS API (no interface endpoints). This is why
  the API key is passed as an environment variable rather than fetched from Secrets Manager.
- Alerts are computed once, in the DB transaction, and travel as the function's return value
  (JSON, Decimals as strings). If they ever exceeded 256 KB the return value would be capped.
