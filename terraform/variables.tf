# =============================================================================
# Foundation variables (always used)
# =============================================================================

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone for zonal resources"
  type        = string
  default     = "us-central1-a"
}

variable "org_name" {
  description = "Organization name, used for resource naming"
  type        = string
}

variable "environment" {
  description = "Environment name (production, staging, dev)"
  type        = string
  default     = "production"
}

# =============================================================================
# Cloud SQL configuration
# =============================================================================

variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "db_pitr_retention_days" {
  description = "Point-in-time recovery retention in days (minimum 7)"
  type        = number
  default     = 30

  validation {
    condition     = var.db_pitr_retention_days >= 7
    error_message = "PITR retention must be at least 7 days (ADR-004)."
  }
}

variable "db_backup_retention_days" {
  description = "Snapshot backup retention in days (minimum 30)"
  type        = number
  default     = 90

  validation {
    condition     = var.db_backup_retention_days >= 30
    error_message = "Backup retention must be at least 30 days (ADR-004)."
  }
}

# =============================================================================
# GCS lifecycle configuration
# =============================================================================

variable "raw_data_nearline_after_days" {
  description = "Days before raw data transitions to Nearline storage"
  type        = number
  default     = 90
}

variable "noncurrent_version_retention_days" {
  description = "Days to retain non-current object versions"
  type        = number
  default     = 30
}

# =============================================================================
# Optional component feature flags (all default false)
# =============================================================================

variable "enable_slurm" {
  description = "Enable SLURM HPC cluster"
  type        = bool
  default     = false
}

variable "enable_filestore" {
  description = "Enable Filestore NFS"
  type        = bool
  default     = false
}

variable "enable_jupyter" {
  description = "Enable JupyterHub"
  type        = bool
  default     = false
}

variable "enable_rstudio" {
  description = "Enable RStudio Server"
  type        = bool
  default     = false
}

variable "enable_nextflow" {
  description = "Enable Nextflow orchestration"
  type        = bool
  default     = false
}

variable "enable_snakemake" {
  description = "Enable Snakemake orchestration"
  type        = bool
  default     = false
}

variable "enable_cellxgene" {
  description = "Enable cellxgene visualization"
  type        = bool
  default     = false
}

variable "enable_meilisearch" {
  description = "Enable Meilisearch document search"
  type        = bool
  default     = false
}

variable "enable_qc_dashboard" {
  description = "Enable QC Dashboard"
  type        = bool
  default     = false
}

# =============================================================================
# SLURM configuration (used when enable_slurm = true)
# =============================================================================

variable "slurm_max_nodes_standard" {
  description = "Maximum number of standard compute nodes"
  type        = number
  default     = 20
}

variable "slurm_instance_type_standard" {
  description = "Instance type for standard compute nodes"
  type        = string
  default     = "n2-highmem-8"
}

variable "slurm_use_spot_standard" {
  description = "Use spot/preemptible VMs for standard partition"
  type        = bool
  default     = true
}

variable "slurm_max_nodes_interactive" {
  description = "Maximum number of interactive compute nodes"
  type        = number
  default     = 5
}

variable "slurm_instance_type_interactive" {
  description = "Instance type for interactive compute nodes"
  type        = string
  default     = "n2-standard-4"
}

variable "slurm_idle_timeout_minutes" {
  description = "Minutes of idle time before SLURM nodes are terminated"
  type        = number
  default     = 10
}

# =============================================================================
# Filestore configuration (used when enable_filestore = true)
# =============================================================================

variable "filestore_capacity_gb" {
  description = "Filestore NFS capacity in GB"
  type        = number
  default     = 1024
}

# =============================================================================
# Notebook configuration (used when enable_jupyter / enable_rstudio = true)
# =============================================================================

variable "jupyter_cpu_limit" {
  description = "Max CPU per Jupyter session"
  type        = string
  default     = "4"
}

variable "jupyter_memory_limit" {
  description = "Max memory per Jupyter session"
  type        = string
  default     = "8Gi"
}

variable "rstudio_cpu_limit" {
  description = "Max CPU per RStudio session"
  type        = string
  default     = "4"
}

variable "rstudio_memory_limit" {
  description = "Max memory per RStudio session"
  type        = string
  default     = "8Gi"
}

variable "session_idle_timeout_hours" {
  description = "Hours of idle time before notebook sessions are auto-stopped"
  type        = number
  default     = 4
}

# =============================================================================
# GKE configuration
# =============================================================================

variable "gke_authorized_networks" {
  description = "CIDR blocks authorized to access the GKE control plane"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

# =============================================================================
# SA hardening: bioAF service account emails
# =============================================================================
# Both SAs are created by install-gcp.sh before this Terraform runs. The
# emails are passed in so module bindings (per-secret, per-subscription,
# per-tag) can reference the correct identity. bioaf-app is the runtime
# data-plane SA attached to the GCE VM. bioaf-bootstrap is impersonated
# only by services that need broad IAM/build/Cloud-Build permissions.

variable "bioaf_app_sa_email" {
  description = "Email of the bioaf-app service account (runtime data plane)."
  type        = string
  default     = ""
}

variable "bioaf_bootstrap_sa_email" {
  description = "Email of the bioaf-bootstrap service account (impersonated for IAM/Cloud Build)."
  type        = string
  default     = ""
}
