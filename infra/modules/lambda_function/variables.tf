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

variable "in_vpc" {
  type    = bool
  default = false
}

variable "subnet_ids" {
  type    = list(string)
  default = []
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}

variable "reserved_concurrency" {
  type    = number
  default = -1
}
