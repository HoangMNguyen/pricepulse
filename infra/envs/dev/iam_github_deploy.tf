# What the deploy role may do outside IAM: exactly the calls Terraform makes to refresh and apply
# this stack, plus the deploy workflow's own steps (state, migrate invoke, CDN invalidation). It
# replaces the AWS-managed PowerUserAccess, which reached every bucket, distribution and table in
# the account. IAM stays in github_iam (iam_github.tf); the boundary stays the ceiling.
#
# Scoping, in order of preference:
#   - by pricepulse-dev-* name where the ARN carries a name (functions, layers, log groups,
#     alarms, topics, parameters, schedules, the raw bucket);
#   - by the Project=pricepulse tag (default_tags) for id-addressed resources whose service
#     evaluates aws:ResourceTag (CloudFront distributions, ACM certificates, API Gateway tags);
#   - by the exact id, read from this stack, where neither works (Route 53 zone, CloudFront cache
#     and response headers policies, the HTTP API and its untaggable routes/integrations/stages,
#     the AWS-managed KMS keys);
#   - "*" only where the action has no resource-level permission; each case is commented.
#
# Two customer-managed policies, split by area: core (state, data plane, compute) and edge (CDN,
# DNS, API). Inline would not fit: a role's inline policies share a 10,240-character quota and
# these two plus github_iam exceed it; each managed policy stays under its 6,144 limit. The role
# could otherwise edit pricepulse-* policies (ManageProjectRoles), so github_iam and the boundary
# deny every write to policy/pricepulse-dev-github-deploy-*, and the role cannot attach or detach
# its own policies either (ReadOnlyOnOwnTrust). Changes here, and replacements of the id-pinned
# resources (their ids feed these policies), are therefore a human apply, as ADR-0006 already
# requires for the role:
#   terraform -chdir=infra/envs/dev apply \
#     -target=aws_iam_role_policy_attachment.github_deploy_core \
#     -target=aws_iam_role_policy_attachment.github_deploy_edge

locals {
  state_bucket  = "pricepulse-tfstate-${local.account_id}"
  state_key     = "dev/terraform.tfstate"
  lambda_prefix = "arn:aws:lambda:${var.region}:${local.account_id}"
  logs_prefix   = "arn:aws:logs:${var.region}:${local.account_id}:log-group"
  ses_identities = [
    for e in toset(concat([var.ses_sender], var.alert_recipients)) :
    "arn:aws:ses:${var.region}:${local.account_id}:identity/${e}"
  ]
}

# Lambda reads and writes function environment variables with the AWS-managed aws/lambda key,
# using the caller's credentials (GetFunction decrypts them for the plan).
data "aws_kms_alias" "lambda" {
  name = "alias/aws/lambda"
}

data "aws_iam_policy_document" "github_deploy_core" {
  # --- Terraform state: only this environment's key and its lock file --------------------------
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.state_bucket}"]
    # dev/* for the state; env:/* is the S3 backend's workspace listing at init.
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["dev/*", "env:/*"]
    }
  }
  statement {
    sid     = "StateReadWrite"
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [
      "arn:aws:s3:::${local.state_bucket}/${local.state_key}",
      "arn:aws:s3:::${local.state_bucket}/${local.state_key}.tflock",
    ]
  }
  statement {
    sid       = "StateUnlock"
    actions   = ["s3:DeleteObject"]
    resources = ["arn:aws:s3:::${local.state_bucket}/${local.state_key}.tflock"]
  }

  # --- Raw bucket (configuration only; the functions own the objects) --------------------------
  statement {
    sid = "RawBucket"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      # HeadBucket, the provider's existence check on every refresh, is authorized as ListBucket.
      # Without it the read gets a 403 that the provider reports as "bucket deleted", and the
      # plan then recreates the bucket. (A data event, so it never showed in CloudTrail history.)
      "s3:ListBucket",
      "s3:GetBucket*",
      "s3:GetAccelerateConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutEncryptionConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketNotification",
      "s3:PutBucketTagging",
      "s3:ListTagsForResource",
      "s3:TagResource",
      "s3:UntagResource",
    ]
    resources = ["arn:aws:s3:::${local.name}-raw-${local.account_id}"]
  }

  # --- Lambda --------------------------------------------------------------------------------------
  statement {
    sid = "Functions"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:ListVersionsByFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PutFunctionConcurrency",
      "lambda:DeleteFunctionConcurrency",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:GetFunctionEventInvokeConfig",
      "lambda:PutFunctionEventInvokeConfig",
      "lambda:UpdateFunctionEventInvokeConfig",
      "lambda:DeleteFunctionEventInvokeConfig",
      "lambda:ListTags",
      "lambda:TagResource",
      "lambda:UntagResource",
      # The workflow's migrate step.
      "lambda:InvokeFunction",
    ]
    resources = ["${local.lambda_prefix}:function:${local.name}-*"]
  }
  statement {
    sid       = "LayerPublish"
    actions   = ["lambda:PublishLayerVersion"]
    resources = ["${local.lambda_prefix}:layer:${local.name}-deps"]
  }
  statement {
    sid       = "LayerVersions"
    actions   = ["lambda:GetLayerVersion", "lambda:DeleteLayerVersion"]
    resources = ["${local.lambda_prefix}:layer:${local.name}-deps:*"]
  }

  # --- CloudWatch Logs and alarms ------------------------------------------------------------------
  statement {
    sid = "LogGroups"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:ListTagsForResource",
      "logs:TagResource",
      "logs:UntagResource",
      "logs:ListTagsLogGroup",
      "logs:TagLogGroup",
      "logs:UntagLogGroup",
    ]
    resources = [
      "${local.logs_prefix}:/aws/lambda/${local.name}-*",
      "${local.logs_prefix}:/aws/apigateway/${local.name}",
      "${local.logs_prefix}:/aws/apigateway/${local.name}:*",
    ]
  }
  # DescribeLogGroups is a list call authorized against the account's log-group:* pattern; it
  # returns metadata (names, retention), never log data.
  statement {
    sid       = "LogGroupsList"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["${local.logs_prefix}:*"]
  }
  # HTTP API access logging: API Gateway creates a vended log delivery with the caller's
  # credentials. These delivery and resource-policy actions have no resource-level permission.
  statement {
    sid = "ApiAccessLogDelivery"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
    ]
    resources = ["*"]
  }
  statement {
    sid = "Alarms"
    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListTagsForResource",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
    ]
    resources = ["arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:${local.name}-*"]
  }

  # --- SNS (the pattern also covers the topic's subscription ARNs, <topic>:<uuid>) ----------------
  statement {
    sid = "AlarmsTopic"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:Subscribe",
      "sns:Unsubscribe",
      "sns:GetSubscriptionAttributes",
      "sns:SetSubscriptionAttributes",
      "sns:ListTagsForResource",
      "sns:TagResource",
      "sns:UntagResource",
    ]
    resources = ["arn:aws:sns:${var.region}:${local.account_id}:${local.name}-*"]
  }

  # --- SSM parameters and the AWS-managed keys ----------------------------------------------------
  statement {
    sid = "Parameters"
    actions = [
      "ssm:PutParameter",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:DeleteParameter",
      "ssm:ListTagsForResource",
      "ssm:AddTagsToResource",
      "ssm:RemoveTagsFromResource",
    ]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/pricepulse/dev/*"]
  }
  # DescribeParameters has no resource-level permission (names and metadata only, no values).
  statement {
    sid       = "ParametersDescribe"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }
  statement {
    sid       = "SsmKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "LambdaKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [data.aws_kms_alias.lambda.target_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["lambda.${var.region}.amazonaws.com"]
    }
  }
  # The aws_kms_alias lookups: DescribeKey on the two keys; ListAliases has no resource-level
  # permission.
  statement {
    sid       = "KeyDescribe"
    actions   = ["kms:DescribeKey"]
    resources = [data.aws_kms_alias.ssm.target_key_arn, data.aws_kms_alias.lambda.target_key_arn]
  }
  statement {
    sid       = "KeyAliasesList"
    actions   = ["kms:ListAliases"]
    resources = ["*"]
  }

  # --- EventBridge Scheduler (PassRole on the scheduler role is in github_iam) --------------------
  statement {
    sid = "Schedules"
    actions = [
      "scheduler:CreateSchedule",
      "scheduler:UpdateSchedule",
      "scheduler:DeleteSchedule",
      "scheduler:GetSchedule",
      "scheduler:CreateScheduleGroup",
      "scheduler:DeleteScheduleGroup",
      "scheduler:GetScheduleGroup",
      "scheduler:ListTagsForResource",
      "scheduler:TagResource",
      "scheduler:UntagResource",
    ]
    resources = [
      "arn:aws:scheduler:${var.region}:${local.account_id}:schedule-group/${local.name}",
      "arn:aws:scheduler:${var.region}:${local.account_id}:schedule/${local.name}/*",
    ]
  }

  # --- SES identities: the configured sender and recipients, by exact address --------------------
  statement {
    sid = "EmailIdentities"
    actions = [
      "ses:CreateEmailIdentity",
      "ses:DeleteEmailIdentity",
      "ses:GetEmailIdentity",
      "ses:ListTagsForResource",
      "ses:TagResource",
      "ses:UntagResource",
    ]
    resources = local.ses_identities
  }
  # checkov:skip=CKV_AWS_356: "*" only for DescribeParameters, ListAliases and the log-delivery actions, which have no resource-level permission
  # checkov:skip=CKV_AWS_111: logs:PutResourcePolicy and the log-delivery writes have no resource-level permission; every other write is pinned by name
}

data "aws_iam_policy_document" "github_deploy_edge" {
  # --- CloudFront ----------------------------------------------------------------------------------
  # Distribution ARNs carry an AWS-generated id, so they are matched by the Project tag the
  # provider's default_tags send on create; hoangmnguyen.com's distribution is out of reach.
  statement {
    sid = "Distribution"
    actions = [
      "cloudfront:GetDistribution",
      "cloudfront:GetDistributionConfig",
      "cloudfront:UpdateDistribution",
      "cloudfront:DeleteDistribution",
      "cloudfront:CreateInvalidation",
      "cloudfront:GetInvalidation",
      "cloudfront:ListTagsForResource",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
    ]
    resources = ["arn:aws:cloudfront::${local.account_id}:distribution/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["pricepulse"]
    }
  }
  statement {
    sid       = "DistributionCreate"
    actions   = ["cloudfront:CreateDistribution"]
    resources = ["arn:aws:cloudfront::${local.account_id}:distribution/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["pricepulse"]
    }
  }
  # Tag-on-create (CreateDistributionWithTags) of a distribution that has no tags yet.
  statement {
    sid       = "DistributionTagOnCreate"
    actions   = ["cloudfront:TagResource"]
    resources = ["arn:aws:cloudfront::${local.account_id}:distribution/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["pricepulse"]
    }
    condition {
      test     = "Null"
      variable = "aws:ResourceTag/Project"
      values   = ["true"]
    }
  }
  # Cache and response headers policies are untaggable and their Create* actions have no
  # resource-level permission: CI reads and updates exactly this stack's two; a replacement is a
  # human apply.
  statement {
    sid = "CdnPolicies"
    actions = [
      "cloudfront:GetCachePolicy",
      "cloudfront:GetCachePolicyConfig",
      "cloudfront:UpdateCachePolicy",
      "cloudfront:GetResponseHeadersPolicy",
      "cloudfront:GetResponseHeadersPolicyConfig",
      "cloudfront:UpdateResponseHeadersPolicy",
    ]
    resources = [
      aws_cloudfront_cache_policy.reads.arn,
      aws_cloudfront_response_headers_policy.security.arn,
    ]
  }
  # The AWS-managed policies the distribution references, read by id after a lookup by name.
  statement {
    sid     = "ManagedCdnPoliciesRead"
    actions = ["cloudfront:GetCachePolicy", "cloudfront:GetOriginRequestPolicy"]
    resources = [
      data.aws_cloudfront_cache_policy.disabled.arn,
      data.aws_cloudfront_origin_request_policy.all_viewer_except_host.arn,
    ]
  }
  # The by-name lookups list policies; List* has no resource-level permission.
  statement {
    sid       = "CdnPoliciesList"
    actions   = ["cloudfront:ListCachePolicies", "cloudfront:ListOriginRequestPolicies"]
    resources = ["*"]
  }

  # --- ACM (CloudFront certificates live in us-east-1) --------------------------------------------
  # RequestCertificate has no resource-level permission; the condition keys pin what it may ask for.
  dynamic "statement" {
    for_each = local.has_domain ? [local.hostnames] : []
    content {
      sid       = "CertificateRequest"
      actions   = ["acm:RequestCertificate"]
      resources = ["*"]
      condition {
        test     = "ForAllValues:StringEquals"
        variable = "acm:DomainNames"
        values   = statement.value
      }
      condition {
        test     = "StringEquals"
        variable = "acm:ValidationMethod"
        values   = ["DNS"]
      }
    }
  }
  # Reads of this project's certificates, and of a just-requested one before its tags land.
  statement {
    sid       = "CertificateRead"
    actions   = ["acm:DescribeCertificate", "acm:ListTagsForCertificate"]
    resources = ["arn:aws:acm:${var.region}:${local.account_id}:certificate/*"]
    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:ResourceTag/Project"
      values   = ["pricepulse"]
    }
  }
  statement {
    sid       = "CertificateTagOnCreate"
    actions   = ["acm:AddTagsToCertificate"]
    resources = ["arn:aws:acm:${var.region}:${local.account_id}:certificate/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["pricepulse"]
    }
    condition {
      test     = "Null"
      variable = "aws:ResourceTag/Project"
      values   = ["true"]
    }
  }
  statement {
    sid = "CertificateManage"
    actions = [
      "acm:AddTagsToCertificate",
      "acm:RemoveTagsFromCertificate",
      "acm:DeleteCertificate",
    ]
    resources = ["arn:aws:acm:${var.region}:${local.account_id}:certificate/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["pricepulse"]
    }
  }

  # --- Route 53: this stack's zone by id (Route 53 has no tag conditions). Creating or deleting
  # the zone changes the NS delegation at the parent DNS host, a manual step anyway, so it stays
  # a human apply (CreateHostedZone has no resource-level permission).
  dynamic "statement" {
    for_each = local.has_domain ? [local.zone_id] : []
    content {
      sid = "HostedZone"
      actions = [
        "route53:GetHostedZone",
        "route53:ListResourceRecordSets",
        "route53:ChangeResourceRecordSets",
        "route53:ListTagsForResource",
        "route53:ChangeTagsForResource",
      ]
      resources = ["arn:aws:route53:::hostedzone/${statement.value}"]
    }
  }
  # Record changes are polled by change id, which is random per change and untaggable.
  statement {
    sid       = "RecordChanges"
    actions   = ["route53:GetChange"]
    resources = ["arn:aws:route53:::change/*"]
  }

  # --- API Gateway (HTTP API): the API by id; its routes, integrations and stages are untaggable
  # children of it. A replacement of the API is a human apply.
  statement {
    sid = "HttpApi"
    actions = [
      "apigateway:GET",
      "apigateway:POST",
      "apigateway:PUT",
      "apigateway:PATCH",
      "apigateway:DELETE",
    ]
    resources = [
      "arn:aws:apigateway:${var.region}::/apis/${aws_apigatewayv2_api.main.id}",
      "arn:aws:apigateway:${var.region}::/apis/${aws_apigatewayv2_api.main.id}/*",
    ]
  }
  # Tag changes address /tags/<url-encoded resource ARN>; fenced by the API's Project tag.
  statement {
    sid       = "HttpApiTags"
    actions   = ["apigateway:GET", "apigateway:POST", "apigateway:DELETE"]
    resources = ["arn:aws:apigateway:${var.region}::/tags/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["pricepulse"]
    }
  }
  # checkov:skip=CKV_AWS_356: "*" only for ListCachePolicies, ListOriginRequestPolicies and RequestCertificate, which have no resource-level permission
  # checkov:skip=CKV_AWS_111: id-addressed writes are fenced by the Project tag or pinned to exact ids
}

resource "aws_iam_policy" "github_deploy_core" {
  name        = "${local.name}-github-deploy-core"
  description = "pricepulse deploy role: state, raw bucket, Lambda, logs, alarms, SNS, SSM, Scheduler, SES"
  policy      = data.aws_iam_policy_document.github_deploy_core.json
}

resource "aws_iam_policy" "github_deploy_edge" {
  name        = "${local.name}-github-deploy-edge"
  description = "pricepulse deploy role: CloudFront, ACM, Route 53, API Gateway"
  policy      = data.aws_iam_policy_document.github_deploy_edge.json
}

resource "aws_iam_role_policy_attachment" "github_deploy_core" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy_core.arn
}

resource "aws_iam_role_policy_attachment" "github_deploy_edge" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy_edge.arn
}
