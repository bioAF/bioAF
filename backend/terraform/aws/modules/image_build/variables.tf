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

variable "role_paths" {
  type        = map(string)
  default     = {}
  description = "IAM path of each existing role in state, by name. The backend reads it from state and drops roles deleted in AWS, so existing roles never move and a missing one is recreated under new_role_path (plan 04B)."
}

variable "new_role_path" {
  type        = string
  default     = "/"
  description = "IAM path for roles and instance profiles not yet in state. The backend passes /bioaf/; the default only matters when it does not (destroy, output reads)."
}
