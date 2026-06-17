variable "region" {
  description = "AWS region for the foundation buckets"
  type        = string
}

variable "state_bucket_name" {
  description = "Name of the S3 bucket to store Terraform state (globally unique; the executor computes it)"
  type        = string
}

variable "backups_bucket_name" {
  description = "Name of the persistent backups bucket. Defaults to bioaf-backups-<account_id>. Fixed naming (no stack_uid) so it survives storage teardown/redeploy."
  type        = string
  default     = ""
}

variable "account_id" {
  description = "AWS account id, used only to build the default backups bucket name when backups_bucket_name is empty."
  type        = string
  default     = ""
}
