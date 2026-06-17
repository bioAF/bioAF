# Output names match the GCS storage module exactly: the executor's
# read_module_outputs maps each to the platform_config `<store>_bucket_name`
# key that S3StorageProvider._get_bucket_config reads, so the app is cloud-blind.

output "ingest_bucket_name" {
  description = "Name of the ingest S3 bucket"
  value       = aws_s3_bucket.store["ingest"].bucket
}

output "raw_bucket_name" {
  description = "Name of the raw data S3 bucket"
  value       = aws_s3_bucket.store["raw"].bucket
}

output "working_bucket_name" {
  description = "Name of the working data S3 bucket"
  value       = aws_s3_bucket.store["working"].bucket
}

output "results_bucket_name" {
  description = "Name of the results S3 bucket"
  value       = aws_s3_bucket.store["results"].bucket
}

output "references_bucket_name" {
  description = "Name of the reference-data S3 bucket"
  value       = aws_s3_bucket.store["references"].bucket
}

output "literature_bucket_name" {
  description = "Name of the Literature S3 bucket (Paper PDFs, extracted text, page images)"
  value       = aws_s3_bucket.store["literature"].bucket
}

output "config_backups_bucket_name" {
  description = "Name of the config backups S3 bucket"
  value       = aws_s3_bucket.store["config_backups"].bucket
}
