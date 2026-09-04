# Private-only VPC: no internet gateway, no NAT. The only egress is the free S3 gateway endpoint.
# Functions that need the internet (scrape, notify) run outside the VPC. See ADR-0003.

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
  # checkov:skip=CKV2_AWS_11: VPC flow logs cost money; nothing in this VPC talks to the internet
  # checkov:skip=CKV2_AWS_12: default SG is unused; every resource has an explicit SG
}

resource "aws_subnet" "private" {
  for_each          = { a = "10.42.1.0/24", b = "10.42.2.0/24" }
  vpc_id            = aws_vpc.main.id
  cidr_block        = each.value
  availability_zone = "${local.region}${each.key}"
  tags              = { Name = "${local.name}-private-${each.key}" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "${local.name}-s3" }
}

resource "aws_security_group" "lambda" {
  name        = "${local.name}-lambda"
  description = "Lambdas inside the VPC: Postgres to the DB SG and HTTPS to the S3 endpoint only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-lambda" }
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_db" {
  security_group_id            = aws_security_group.lambda.id
  description                  = "PostgreSQL to Aurora"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.db.id
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_s3" {
  security_group_id = aws_security_group.lambda.id
  description       = "HTTPS to the S3 gateway endpoint"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Aurora: accepts PostgreSQL from the Lambda SG only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-db" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_lambda" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from Lambdas"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.lambda.id
}
