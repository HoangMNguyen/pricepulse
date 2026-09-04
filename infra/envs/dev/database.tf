# Aurora PostgreSQL Serverless v2 that scales to zero. IAM auth only for the app roles; the
# master password lives in Secrets Manager and is used solely by scripts/bootstrap_db.sh over
# the Data API. See ADR-0002.

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = [for s in aws_subnet.private : s.id]
}

resource "aws_rds_cluster" "main" {
  cluster_identifier = local.name
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"
  engine_version     = var.aurora_engine_version
  database_name      = "pricepulse"

  master_username             = "pricepulse_admin"
  manage_master_user_password = true

  iam_database_authentication_enabled = true
  enable_http_endpoint                = true
  storage_encrypted                   = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  backup_retention_period = 1
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true

  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = 1
    seconds_until_auto_pause = 300
  }

  # checkov:skip=CKV_AWS_139: deletion protection off on purpose: `terraform destroy` must be clean
  # checkov:skip=CKV_AWS_162: IAM auth IS enabled (checkov false positive on aurora-postgresql)
  # checkov:skip=CKV_AWS_327: default RDS KMS key is fine for a personal project
  # checkov:skip=CKV_AWS_96: encryption is enabled via storage_encrypted
  # checkov:skip=CKV2_AWS_8: 1-day backups are enough; raw S3 payloads allow full rebuild
  # checkov:skip=CKV2_AWS_27: query logging costs more than the cluster
  # checkov:skip=CKV_AWS_128: see CKV_AWS_162
  # checkov:skip=CKV_AWS_324: log exports disabled to stay within budget
  # checkov:skip=CKV_AWS_313: copy_tags_to_snapshot irrelevant with 1-day retention
  # checkov:skip=CKV_AWS_338: 14-day log retention chosen deliberately
}

resource "aws_rds_cluster_instance" "main" {
  identifier                 = "${local.name}-1"
  cluster_identifier         = aws_rds_cluster.main.id
  instance_class             = "db.serverless"
  engine                     = aws_rds_cluster.main.engine
  engine_version             = aws_rds_cluster.main.engine_version
  publicly_accessible        = false
  auto_minor_version_upgrade = true
  # checkov:skip=CKV_AWS_354: performance insights not needed
  # checkov:skip=CKV_AWS_353: performance insights not needed
  # checkov:skip=CKV_AWS_118: enhanced monitoring costs money
}

locals {
  db_user_arn_prefix = "arn:aws:rds-db:${local.region}:${local.account_id}:dbuser:${aws_rds_cluster.main.cluster_resource_id}"
}
