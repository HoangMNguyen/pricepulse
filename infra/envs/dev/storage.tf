# Bronze layer: verbatim retailer responses. Every ObjectCreated under raw/ triggers `process`.

resource "aws_s3_bucket" "raw" {
  bucket = "${local.name}-raw-${local.account_id}"
  # checkov:skip=CKV_AWS_144: no cross-region replication for a personal project
  # checkov:skip=CKV_AWS_18: access logs would cost more than the data
  # checkov:skip=CKV_AWS_145: SSE-S3 is sufficient
  # checkov:skip=CKV_AWS_21: raw payloads are immutable and re-fetchable; versioning adds cost
  # checkov:skip=CKV2_AWS_62: event notifications ARE configured below (Lambda target)
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  # checkov:skip=CKV_AWS_300: abort_incomplete_multipart_upload IS set (7 days) inside the filtered rule
  bucket = aws_s3_bucket.raw.id
  rule {
    id     = "expire-raw"
    status = "Enabled"
    filter {
      prefix = "raw/"
    }
    expiration {
      days = 365
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_lambda_permission" "s3_invoke_process" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = module.process.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw.arn
}

resource "aws_s3_bucket_notification" "raw" {
  bucket = aws_s3_bucket.raw.id
  lambda_function {
    lambda_function_arn = module.process.function_arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/"
    filter_suffix       = ".json.gz"
  }
  depends_on = [aws_lambda_permission.s3_invoke_process]
}
