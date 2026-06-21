variable "region" {
  description = "AWS region for the CodeBuild project + ECR repos"
  type        = string
}

variable "account_id" {
  description = "AWS account id (used to scope the ECR repository ARNs)"
  type        = string
}

variable "org_slug" {
  description = "Organization slug (carried for naming parity; current names are install-level/stable)"
  type        = string
  default     = "bioaf"
}
