variable "region" {
  type    = string
  default = "us-east-1"
}

variable "env" {
  type        = string
  description = "dev or prod"
}

variable "reports_bucket_name" {
  type        = string
  description = "Existing S3 bucket used by Report Studio"
}

variable "table_name" {
  type        = string
  description = "Existing DynamoDB table where Report Studio stores report metadata"
}

variable "password_param" {
  type        = string
  description = "SSM SecureString parameter holding the dashboard password (created by hand, never by Terraform)"
}

variable "exclude_ids" {
  type        = string
  default     = "1"
  description = "Comma-separated device IDs that are not children (admin, teachers). Never counted."
}

variable "admin_password_param" {
  type        = string
  description = "SSM SecureString holding the register-upload (admin) password, created by hand"
}
