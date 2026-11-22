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

variable "bucket_markers" {
  description = "bioAF's identity marker per store (written as the bioaf_marker tag). bioAF records each marker with the bucket's identity, so a bucket deleted and recreated under the same name is told apart. Empty: no marker is written."
  type        = map(string)
  default     = {}
}
