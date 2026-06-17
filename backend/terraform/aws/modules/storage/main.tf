terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {} # Configured dynamically by the executor (S3 state, the AWS analog of the GCS backend)
}

provider "aws" {
  region = var.region
  # No explicit credentials: on AWS the app runs on an EC2 VM whose bioaf-app
  # instance profile is the ambient credential (the same role S3StorageProvider
  # authenticates through). This mirrors how the GCP executor injects SA creds,
  # only the mechanism differs - the BAL/per-provider split applied to infra.
}

locals {
  bucket_prefix = "bioaf"

  # Logical store -> purpose tag. The *_bucket_name outputs the app reads as
  # platform_config keys use the underscore key (e.g. config_backups), while the
  # bucket name itself uses hyphens (bioaf-config-backups-...), matching GCP.
  stores = {
    ingest         = { purpose = "ingest" }
    raw            = { purpose = "raw-data" }
    working        = { purpose = "working-data" }
    results        = { purpose = "results" }
    references     = { purpose = "references" }
    literature     = { purpose = "literature" }
    config_backups = { purpose = "config-backups" }
  }

  bucket_names = {
    for k, _ in local.stores :
    k => "${local.bucket_prefix}-${replace(k, "_", "-")}-${var.org_slug}-${var.stack_uid}"
  }
}

resource "aws_s3_bucket" "store" {
  for_each = local.stores
  bucket   = local.bucket_names[each.key]

  # force_destroy=false mirrors the GCS module: deletion empties the bucket
  # first (the adapter's delete_bucket does this), never a silent wipe.
  force_destroy = false

  tags = {
    managed_by = "bioaf"
    purpose    = each.value.purpose
  }
}

# Versioning on every bucket (= GCS versioning { enabled = true }).
resource "aws_s3_bucket_versioning" "store" {
  for_each = local.stores
  bucket   = aws_s3_bucket.store[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

# Block all public access. The S3 analog of GCS uniform_bucket_level_access:
# object access is via IAM only, never public ACLs.
resource "aws_s3_bucket_public_access_block" "store" {
  for_each                = local.stores
  bucket                  = aws_s3_bucket.store[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Disable ACLs entirely (bucket-owner-enforced), the other half of GCS uniform
# bucket-level access. Access is IAM-only; the bioaf-app instance profile already
# grants s3:* on bioaf-* (set in install-aws.sh), so no per-bucket policy is
# needed here (unlike GCP, whose default compute SA needs an explicit objectAdmin
# grant in the module).
resource "aws_s3_bucket_ownership_controls" "store" {
  for_each = local.stores
  bucket   = aws_s3_bucket.store[each.key].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Default server-side encryption (SSE-S3 / AES256). GCS encrypts at rest by
# default; S3 requires the explicit configuration.
resource "aws_s3_bucket_server_side_encryption_configuration" "store" {
  for_each = local.stores
  bucket   = aws_s3_bucket.store[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Lifecycle: raw -> STANDARD_IA after 90d, literature -> STANDARD_IA after 180d.
# STANDARD_IA is the S3 infrequent-access tier matching GCS NEARLINE's intent.
resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.store["raw"].id
  rule {
    id     = "raw-to-ia"
    status = "Enabled"
    filter {} # all objects
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "literature" {
  bucket = aws_s3_bucket.store["literature"].id
  rule {
    id     = "literature-to-ia"
    status = "Enabled"
    filter {} # all objects
    transition {
      days          = 180
      storage_class = "STANDARD_IA"
    }
  }
}

# CORS so browsers can upload directly via presigned URLs. ingest = PUT;
# references = PUT + POST (resumable/multipart init), mirroring the GCS CORS.
# S3 handles OPTIONS preflight from these rules, so OPTIONS is not listed.
resource "aws_s3_bucket_cors_configuration" "ingest" {
  bucket = aws_s3_bucket.store["ingest"].id
  cors_rule {
    allowed_origins = ["*"] # tighten to bioAF frontend origins post-MVP
    allowed_methods = ["PUT"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

resource "aws_s3_bucket_cors_configuration" "references" {
  bucket = aws_s3_bucket.store["references"].id
  cors_rule {
    allowed_origins = ["*"] # tighten to bioAF frontend origins post-MVP
    allowed_methods = ["PUT", "POST"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}
