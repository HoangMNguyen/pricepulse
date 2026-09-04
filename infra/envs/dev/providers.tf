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
    neon = {
      source  = "kislerdm/neon"
      version = "~> 0.15"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "pricepulse", Env = "dev", ManagedBy = "terraform" }
  }
}

# Reads NEON_API_KEY from the environment.
provider "neon" {}

data "aws_caller_identity" "current" {}

locals {
  name       = "pricepulse-dev"
  account_id = data.aws_caller_identity.current.account_id
}
