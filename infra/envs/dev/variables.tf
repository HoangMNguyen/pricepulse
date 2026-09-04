variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_recipients" {
  type        = list(string)
  description = "Digest recipients. Each must be SES-verified while the account is in the sandbox."
  validation {
    condition     = length(var.alert_recipients) > 0
    error_message = "At least one recipient is required."
  }
}

variable "ses_sender" {
  type        = string
  description = "Verified SES sender address."
}

variable "github_repo" {
  type        = string
  description = "owner/repo allowed to assume the deploy role via GitHub OIDC."
  default     = "HoangMNguyen/pricepulse"
}

variable "alert_min_discount_pct" {
  type    = number
  default = 20
}

variable "user_agent" {
  type    = string
  default = "pricepulse/0.1"
}

variable "app_package" {
  type    = string
  default = "../../../build/app.zip"
}

variable "layer_package" {
  type    = string
  default = "../../../build/layer.zip"
}

variable "domain_name" {
  type        = string
  description = "Custom domain for the site (apex; www is added). null = *.cloudfront.net only."
  default     = null
}

variable "hosted_zone_id" {
  type        = string
  description = "Existing Route 53 zone for domain_name (e.g. registered through Route 53). null = create one."
  default     = null
}
