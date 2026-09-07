variable "name" {
  type = string
}

variable "handler" {
  type = string
}

variable "package_path" {
  type        = string
  description = "Path to app.zip (package code, no dependencies)."
}

variable "layer_arns" {
  type    = list(string)
  default = []
}

variable "role_policy_json" {
  type        = string
  description = "Inline IAM policy document granting this function's permissions beyond logging/VPC."
}

variable "permissions_boundary" {
  type        = string
  description = "ARN of the permissions boundary attached to the function's role (required: every pricepulse role carries one)."
}

variable "environment" {
  type    = map(string)
  default = {}
}

variable "memory_mb" {
  type    = number
  default = 512
}

variable "timeout_s" {
  type    = number
  default = 60
}

variable "reserved_concurrency" {
  type    = number
  default = -1
}
