# =============================================================================
# GCS Buckets — all with versioning and delete protection (ADR-004)
# =============================================================================

locals {
  bucket_suffix = "${lower(var.org_name)}-${random_id.suffix.hex}"
}

# Raw data bucket
resource "google_storage_bucket" "raw_data" {
  name                        = "bioaf-raw-${local.bucket_suffix}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
    condition {
      age = var.raw_data_nearline_after_days
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = var.noncurrent_version_retention_days
    }
  }
}

# Working data bucket
resource "google_storage_bucket" "working_data" {
  name                        = "bioaf-working-${local.bucket_suffix}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = var.noncurrent_version_retention_days
    }
  }
}

# Results bucket
resource "google_storage_bucket" "results" {
  name                        = "bioaf-results-${local.bucket_suffix}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = var.noncurrent_version_retention_days
    }
  }
}

# DEPRECATED: Backups now go to the persistent backups bucket in the
# foundation module. Kept for backward compatibility with existing deploys.
resource "google_storage_bucket" "config_backups" {
  name                        = "bioaf-config-backups-${local.bucket_suffix}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = var.noncurrent_version_retention_days
    }
  }
}
