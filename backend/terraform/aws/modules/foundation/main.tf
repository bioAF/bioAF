terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # No backend block: the foundation bootstraps the S3 Terraform-state bucket
  # itself, so it runs with LOCAL state (the executor applies it with
  # local_backend=True), exactly like the GCP foundation bootstraps the GCS
  # state bucket. Every other AWS module then uses this bucket as its S3 backend.
}

provider "aws" {
  region = var.region
}

locals {
  backups_bucket = var.backups_bucket_name != "" ? var.backups_bucket_name : "bioaf-backups-${var.account_id}"

  # The S3 analog of the GCS state + backups buckets. Both get versioning (state
  # history / recoverable backups), block-public-access, BucketOwnerEnforced,
  # SSE-S3, and a keep-last-10-noncurrent-versions lifecycle (= the GCS
  # num_newer_versions=10 rule).
  buckets = {
    terraform_state = { name = var.state_bucket_name, purpose = "terraform-state" }
    backups         = { name = local.backups_bucket, purpose = "backups" }
  }
}

resource "aws_s3_bucket" "this" {
  for_each      = local.buckets
  bucket        = each.value.name
  force_destroy = false
  tags = {
    managed_by = "bioaf"
    purpose    = each.value.purpose
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = aws_s3_bucket.this[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    id     = "expire-old-noncurrent-versions"
    status = "Enabled"
    filter {} # all objects
    noncurrent_version_expiration {
      newer_noncurrent_versions = 10
      noncurrent_days           = 30
    }
  }
}
