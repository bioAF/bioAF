variable "region" {
  description = "AWS region for all storage buckets"
  type        = string
}

variable "org_slug" {
  description = "Organization slug used in bucket naming"
  type        = string
}

variable "stack_uid" {
  description = "Short unique ID appended to bucket names for global S3 uniqueness and redeploy safety. Defaults to 'pending' so terraform destroy works from state without requiring the original value."
  type        = string
  default     = "pending"
}
