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

variable "role_path" {
  type        = string
  default     = "/"
  description = "IAM path for the roles (and instance profiles) this module creates. The backend passes /bioaf/ for new roles and the existing path for roles already in state (plan 04B)."
}
