terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "pricepulse", Env = "dev", ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name       = "pricepulse-dev"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
}
