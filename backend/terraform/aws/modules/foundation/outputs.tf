# Output names mirror the GCP foundation module so the executor reads them
# identically (state_bucket_name feeds every other module's S3 backend-config).

output "state_bucket_name" {
  description = "Name of the S3 bucket holding Terraform state"
  value       = aws_s3_bucket.this["terraform_state"].bucket
}

output "state_bucket_url" {
  description = "s3:// URL for the Terraform state bucket"
  value       = "s3://${aws_s3_bucket.this["terraform_state"].bucket}"
}

output "backups_bucket_name" {
  description = "Name of the persistent backups S3 bucket"
  value       = aws_s3_bucket.this["backups"].bucket
}
