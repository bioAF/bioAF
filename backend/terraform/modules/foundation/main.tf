terraform {
  required_version = "~> 1.7"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# GCS bucket for Terraform remote state
resource "google_storage_bucket" "terraform_state" {
  name                        = var.state_bucket_name
  location                    = var.bucket_location
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      num_newer_versions = 10
    }
  }
}

# GCS bucket for persistent backups (pg_dump, config snapshots).
# Uses fixed naming (no stack_uid) so it survives storage teardown/redeploy.
resource "google_storage_bucket" "backups" {
  name                        = var.backups_bucket_name != "" ? var.backups_bucket_name : "bioaf-backups-${var.project_id}"
  location                    = var.bucket_location
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      num_newer_versions = 10
    }
  }
}
