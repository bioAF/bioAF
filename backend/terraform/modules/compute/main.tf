terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  backend "gcs" {}
}

# --- GKE Cluster ---

resource "google_container_cluster" "bioaf" {
  name     = "bioaf-${var.org_slug}-${var.stack_uid}"
  project  = var.project_id
  location = var.region

  # Cluster-level node_locations is the default placement for any pool
  # that does not set its own. All real pools (system/pipelines/
  # interactive/pipeline_head) DO set node_locations = var.k8s_node_zones,
  # so this value only constrains the throwaway default pool created
  # during cluster bootstrap. When gke_default_pool_zone is set (the
  # pre-flight capacity probe picks a healthy zone), the default pool's
  # per-zone IGM is provisioned in that one zone only, sidestepping the
  # "regional default pool needs capacity in every zone simultaneously"
  # failure mode that hangs CREATE_CLUSTER for 70 minutes on any
  # per-zone e2-medium stockout. Empty list means "use all zones in the
  # region" (today's behaviour).
  node_locations = var.gke_default_pool_zone != "" ? [var.gke_default_pool_zone] : []

  # Terraform-managed lifecycle -- teardown handles deletion
  deletion_protection      = false
  remove_default_node_pool = true
  initial_node_count       = 1

  # Minimal default node pool config -- this pool is deleted immediately
  # after cluster creation. pd-standard avoids consuming SSD_TOTAL_GB
  # during the brief bootstrap window (3 zones x 30GB = 90GB) before
  # the pool is torn down.
  node_config {
    disk_size_gb = 30
    disk_type    = "pd-standard"
  }

  # Workload Identity for pod-level GCP auth
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Network policy for pod isolation
  network_policy {
    enabled = true
  }

  resource_labels = {
    managed_by = "bioaf"
    org        = var.org_slug
  }

  # node_locations only constrains the throwaway default pool at create time
  # (see the comment above). A regional cluster picks its own three control
  # plane zones, so after creation the live value rarely matches the requested
  # one. Reconciling it post-create would relocate cluster nodes, so ignore
  # drift here: the field has done its job once the cluster exists.
  lifecycle {
    ignore_changes = [node_locations]
  }
}

# --- Pipeline Node Pool ---

resource "google_container_node_pool" "pipelines" {
  name           = "bioaf-pipelines"
  cluster        = google_container_cluster.bioaf.id
  project        = var.project_id
  location       = var.region
  node_locations = var.k8s_node_zones

  autoscaling {
    min_node_count  = 0
    max_node_count  = var.k8s_pipeline_max_nodes
    location_policy = "ANY"
  }

  node_config {
    machine_type = var.k8s_pipeline_machine_type
    spot         = var.k8s_pipeline_use_spot
    disk_size_gb = 100
    disk_type    = "pd-standard"

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = {
      "bioaf.io/pool" = "pipelines"
    }

    # No taint on pipelines pool: Nextflow K8s executor spawns process
    # pods that cannot carry custom tolerations, so an untainted pool is
    # required. The label + nodeSelector on the head Job still directs
    # orchestrator pods here; other pools' taints prevent Nextflow
    # process pods from landing elsewhere.
  }

  # node_locations requests every zone in the region, but a node pool can only
  # place nodes in the cluster's (three) zones, so GKE drops the extras and the
  # requested set never matches state: a perpetual no-op "update" diff that the
  # additive-update flow keeps re-applying until GKE rejects it with
  # "Must specify a field to update". Ignore the drift; multi-zone fallback
  # still works across whatever zones the cluster actually spans.
  lifecycle {
    ignore_changes = [node_locations]
  }
}

# --- System Node Pool ---
#
# Always-on pool sized for GKE-managed addons only: calico-typha,
# fluentbit, gmp-operator, gke-metadata-server, etc. Without this pool,
# those DaemonSets piggy-back on whichever user pool happens to have a
# node up; calico-typha's 2-replica anti-affinity then pins the pipelines
# pool at 2 nodes whenever any pipeline runs, wasting one full
# n2-highmem-16 node on system addons. With this pool, the pipelines and
# interactive pools can genuinely scale to zero.
#
# Autoscaling uses total_min_node_count / total_max_node_count (global
# counts across the regional cluster's zones), not min_node_count /
# max_node_count (per-zone). Combined with location_policy=ANY and the
# default empty node_locations (= all cluster zones), this lets the
# autoscaler place the floor node in whichever zone has e2-standard-2
# capacity at deploy time, instead of forcing one node per active zone.
# total_min=1 keeps a single node always alive for the addons; HA was
# never an architected goal for the cluster's workloads (see commits
# bde4d604, c399ee21 -- multi-zone was for capacity fallback).
#
# Disk: pd-standard, matching the other pools. On-demand, not spot --
# system addons must not be evicted.
# No taint: GKE-managed DaemonSets do not reliably tolerate custom
# taints. Nextflow process pods are kept off this pool naturally by
# its small size (e2-standard-2 allocatable < typical pipeline requests).

resource "google_container_node_pool" "system" {
  name           = "bioaf-system"
  cluster        = google_container_cluster.bioaf.id
  project        = var.project_id
  location       = var.region
  node_locations = var.k8s_node_zones

  autoscaling {
    total_min_node_count = 1
    total_max_node_count = var.k8s_system_max_nodes
    location_policy      = "ANY"
  }

  node_config {
    machine_type = var.k8s_system_machine_type
    disk_size_gb = 30
    disk_type    = "pd-standard"

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = {
      "bioaf.io/pool" = "system"
    }
  }

  # See bioaf-pipelines: node_locations drifts against the cluster's zones, so
  # ignore it to avoid a perpetual no-op "update".
  lifecycle {
    ignore_changes = [node_locations]
  }
}

# --- Interactive Node Pool ---

resource "google_container_node_pool" "interactive" {
  name           = "bioaf-interactive"
  cluster        = google_container_cluster.bioaf.id
  project        = var.project_id
  location       = var.region
  node_locations = var.k8s_node_zones

  autoscaling {
    min_node_count  = 0
    max_node_count  = var.k8s_interactive_max_nodes
    location_policy = "ANY"
  }

  node_config {
    machine_type = var.k8s_interactive_machine_type
    spot         = false # On-demand for notebook sessions
    disk_size_gb = 100
    disk_type    = "pd-standard"

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = {
      "bioaf.io/pool" = "interactive"
    }

    taint {
      key    = "bioaf.io/pool"
      value  = "interactive"
      effect = "NO_SCHEDULE"
    }
  }

  # See bioaf-pipelines: node_locations drifts against the cluster's zones, so
  # ignore it to avoid a perpetual no-op "update".
  lifecycle {
    ignore_changes = [node_locations]
  }
}

# --- Pipeline Head Node Pool ---
#
# Dedicated on-demand pool for Nextflow head/coordinator pods. Task pods run
# on the cheaper Spot bioaf-pipelines pool, but the head pod has to survive
# the entire pipeline duration: Spot preemption mid-run kills the workflow
# and any in-flight tasks. (cluster-autoscaler.kubernetes.io/safe-to-evict
# only blocks voluntary scale-down; it does NOT protect against Spot
# reclamation.) The taint enforces strict isolation so Nextflow's task
# pods, which cannot carry custom tolerations, can never accidentally
# land on this pool and burn its capacity.

resource "google_container_node_pool" "pipeline_head" {
  name           = "bioaf-pipeline-head"
  cluster        = google_container_cluster.bioaf.id
  project        = var.project_id
  location       = var.region
  node_locations = var.k8s_node_zones

  autoscaling {
    min_node_count  = 0
    max_node_count  = var.k8s_pipeline_head_max_nodes
    location_policy = "ANY"
  }

  node_config {
    machine_type = var.k8s_pipeline_head_machine_type
    disk_size_gb = 30
    disk_type    = "pd-standard"
    spot         = false # On-demand: survives Spot preemption that kills the orchestrator.

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = {
      "bioaf.io/pool" = "pipeline-head"
    }

    taint {
      key    = "bioaf.io/pool"
      value  = "pipeline-head"
      effect = "NO_SCHEDULE"
    }
  }

  # See bioaf-pipelines: node_locations drifts against the cluster's zones, so
  # ignore it to avoid a perpetual no-op "update".
  lifecycle {
    ignore_changes = [node_locations]
  }
}

# --- IAM binding for GCS access from GKE nodes ---

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_iam_member" "gke_storage_access" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "gke_default_node_sa" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "gke_artifact_registry_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

# --- Workload Identity for notebook pods ---
#
# With Workload Identity enabled, pods cannot use the node's default SA.
# Create a dedicated GCP SA for notebook workloads, grant it GCS access,
# and bind it to the bioaf-notebook-runner K8s SA so pods get credentials
# via the metadata server.

resource "google_service_account" "notebook_runner" {
  project      = var.project_id
  account_id   = "bioaf-notebook-runner"
  display_name = "bioAF Notebook Runner"
  description  = "GCP service account for notebook session pods (Workload Identity)"
}

resource "google_project_iam_member" "notebook_runner_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.notebook_runner.email}"
}

resource "google_service_account_iam_member" "notebook_runner_workload_identity" {
  service_account_id = google_service_account.notebook_runner.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[bioaf-notebooks/bioaf-notebook-runner]"

  # The Workload Identity pool (<PROJECT>.svc.id.goog) is registered
  # asynchronously after the cluster's create call returns. Without an
  # explicit depends_on Terraform may schedule this binding before the
  # pool exists, producing 'Identity Pool does not exist' errors.
  depends_on = [
    google_container_cluster.bioaf,
    google_container_node_pool.pipelines,
    google_container_node_pool.interactive,
    google_container_node_pool.system,
  ]
}

# Pipeline runner identity (Workload Identity).
#
# Pipeline pods in bioaf-pipelines run as the bioaf-pipeline-runner KSA on a
# node pool with workload_metadata = GKE_METADATA enforced. Without this GSA
# and its WI binding, Nextflow has no GCP identity and GCS reads fail with
# 'storage.objects.get denied' on bioaf-raw-* / bioaf-results-*.
#
# Object-level access is scoped to bioaf-* buckets via IAM Condition, mirroring
# how bioaf-app is scoped in install-gcp.sh, so this SA cannot reach unrelated
# project data even if its KSA token were exfiltrated.

resource "google_service_account" "pipeline_runner" {
  project      = var.project_id
  account_id   = "bioaf-pipeline-runner"
  display_name = "bioAF Pipeline Runner"
  description  = "GCP service account for Nextflow pipeline pods (Workload Identity)"
}

resource "google_project_iam_member" "pipeline_runner_storage" {
  project = var.project_id
  # storage.admin (not objectAdmin) because Fusion mounts buckets as a
  # filesystem and needs storage.buckets.get for bucket lookup. objectAdmin
  # only covers object-level operations, so task pods would fail at mount
  # time with 'does not have storage.buckets.get access' before .command.sh
  # could even run (exit 126). Matches how bioaf-app is scoped in
  # install-gcp.sh: storage.admin with an IAM Condition on bioaf-* buckets.
  role   = "roles/storage.admin"
  member = "serviceAccount:${google_service_account.pipeline_runner.email}"
  condition {
    title       = "bioaf_buckets_only"
    description = "bioaf_buckets_only"
    expression  = "resource.name.startsWith(\"projects/_/buckets/bioaf-\")"
  }
}

resource "google_service_account_iam_member" "pipeline_runner_workload_identity" {
  service_account_id = google_service_account.pipeline_runner.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[bioaf-pipelines/bioaf-pipeline-runner]"

  # The Workload Identity pool (<PROJECT>.svc.id.goog) is registered
  # asynchronously after the cluster's create call returns. Without an
  # explicit depends_on Terraform may schedule this binding before the
  # pool exists, producing 'Identity Pool does not exist' errors.
  depends_on = [
    google_container_cluster.bioaf,
    google_container_node_pool.pipelines,
    google_container_node_pool.interactive,
    google_container_node_pool.system,
  ]
}

# SA hardening note: bioaf-app's roles/container.admin binding is scoped via
# IAM Condition on the cluster name prefix (resource.name.extract(...) starts
# with "bioaf-") rather than via a Resource Manager tag. GKE clusters are
# regional resources and google_tags_tag_binding (the global tag API) does
# not accept them; google_tags_location_tag_binding works only with the
# project number and has uneven support across GKE features. Name-prefix
# scoping needs no Terraform-side wiring because cluster names are already
# bioaf-*.
