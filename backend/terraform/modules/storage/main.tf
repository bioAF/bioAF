terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  backend "gcs" {} # Configured dynamically by the executor
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  bucket_prefix = "bioaf"
}

resource "google_storage_bucket" "ingest" {
  name          = "${local.bucket_prefix}-ingest-${var.org_slug}-${var.stack_uid}"
  project       = var.project_id
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  versioning { enabled = true }
  force_destroy = true

  # Allow browsers to PUT files directly via signed URLs
  cors {
    origin          = ["*"]
    method          = ["PUT", "OPTIONS"]
    response_header = ["Content-Type", "Content-Length", "Authorization", "x-goog-*"]
    max_age_seconds = 3600
  }

  labels = {
    managed_by = "bioaf"
    purpose    = "ingest"
  }
}

resource "google_storage_bucket" "raw" {
  name          = "${local.bucket_prefix}-raw-${var.org_slug}-${var.stack_uid}"
  project       = var.project_id
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  versioning { enabled = true }
  force_destroy = true

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  labels = {
    managed_by = "bioaf"
    purpose    = "raw-data"
  }
}

resource "google_storage_bucket" "working" {
  name          = "${local.bucket_prefix}-working-${var.org_slug}-${var.stack_uid}"
  project       = var.project_id
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  versioning { enabled = true }
  force_destroy = true

  labels = {
    managed_by = "bioaf"
    purpose    = "working-data"
  }
}

resource "google_storage_bucket" "results" {
  name          = "${local.bucket_prefix}-results-${var.org_slug}-${var.stack_uid}"
  project       = var.project_id
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  versioning { enabled = true }
  force_destroy = true

  labels = {
    managed_by = "bioaf"
    purpose    = "results"
  }
}

resource "google_storage_bucket" "config_backups" {
  name          = "${local.bucket_prefix}-config-backups-${var.org_slug}-${var.stack_uid}"
  project       = var.project_id
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  versioning { enabled = true }
  force_destroy = true

  labels = {
    managed_by = "bioaf"
    purpose    = "config-backups"
  }
}

# --- IAM: grant backend service account object access on all buckets ---

data "google_project" "current" {
  project_id = var.project_id
}

locals {
  # If an explicit SA email is provided use it; otherwise fall back to the
  # project default compute SA (used by GCE VMs created without a custom SA).
  _backend_sa = var.backend_service_account_email != "" ? var.backend_service_account_email : "${data.google_project.current.number}-compute@developer.gserviceaccount.com"
  backend_sa  = "serviceAccount:${local._backend_sa}"

  storage_buckets = {
    ingest         = google_storage_bucket.ingest.name
    raw            = google_storage_bucket.raw.name
    working        = google_storage_bucket.working.name
    results        = google_storage_bucket.results.name
    config_backups = google_storage_bucket.config_backups.name
  }
}

resource "google_storage_bucket_iam_member" "backend_object_admin" {
  for_each = local.storage_buckets
  bucket   = each.value
  role     = "roles/storage.objectAdmin"
  member   = local.backend_sa
}

# --- Pub/Sub for ingest bucket notifications ---

resource "google_pubsub_topic" "ingest_events" {
  name    = "bioaf-ingest-events-${var.org_slug}-${var.stack_uid}"
  project = var.project_id

  labels = {
    managed_by = "bioaf"
    purpose    = "ingest-notifications"
  }
}

resource "google_pubsub_subscription" "ingest_worker" {
  name    = "bioaf-ingest-worker-${var.org_slug}-${var.stack_uid}"
  topic   = google_pubsub_topic.ingest_events.id
  project = var.project_id

  ack_deadline_seconds = 600 # 10 min for large files

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  expiration_policy {
    ttl = "" # Never expire
  }

  labels = {
    managed_by = "bioaf"
  }
}

resource "google_pubsub_topic" "ingest_dead_letter" {
  name    = "bioaf-ingest-dead-letter-${var.org_slug}-${var.stack_uid}"
  project = var.project_id
}

resource "google_pubsub_subscription" "ingest_dead_letter_sub" {
  name    = "bioaf-ingest-dead-letter-sub-${var.org_slug}-${var.stack_uid}"
  topic   = google_pubsub_topic.ingest_dead_letter.id
  project = var.project_id
}

# Bucket notification -> Pub/Sub
resource "google_storage_notification" "ingest_notification" {
  bucket         = google_storage_bucket.ingest.name
  payload_format = "JSON_API_V1"
  topic          = google_pubsub_topic.ingest_events.id
  event_types    = ["OBJECT_FINALIZE"]

  depends_on = [google_pubsub_topic_iam_member.gcs_publisher]
}

# Allow GCS to publish to the Pub/Sub topic
data "google_storage_project_service_account" "gcs_account" {
  project = var.project_id
}

resource "google_pubsub_topic_iam_member" "gcs_publisher" {
  topic   = google_pubsub_topic.ingest_events.id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs_account.email_address}"
  project = var.project_id
}
