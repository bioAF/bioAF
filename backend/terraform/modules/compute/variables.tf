variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  description = "GCP region"
}

variable "zone" {
  type        = string
  description = "GCP zone (retained for backward compatibility with tfvars generation)"
}

variable "org_slug" {
  type        = string
  description = "Organization slug used in resource naming"
}

variable "stack_uid" {
  type        = string
  description = "Short unique ID appended to resource names to avoid GCP soft-delete conflicts on redeploy. Defaults to 'pending' so terraform destroy works from state without requiring the original value."
  default     = "pending"
}

variable "k8s_node_zones" {
  type        = list(string)
  default     = []
  description = "Additional zones for node pool placement. When empty, uses the cluster zone only. Set to multiple zones (e.g. [\"us-central1-a\",\"us-central1-b\",\"us-central1-c\"]) so the autoscaler can fall back to another zone when a machine type is unavailable."
}

variable "k8s_pipeline_machine_type" {
  type        = string
  default     = "n2-highmem-16"
  description = "Machine type for the pipeline node pool"
}

variable "k8s_pipeline_max_nodes" {
  type        = number
  default     = 20
  description = "Maximum number of nodes in the pipeline autoscaler"
}

variable "k8s_pipeline_disk_size_gb" {
  type        = number
  default     = 100
  description = <<-EOT
    Boot disk size for each pipeline node, in GB. This is the node's EPHEMERAL storage: the work
    directory, container images, and (with Fusion) the local cache of the cloud work dir all live
    on it. A task that exceeds what is left is EVICTED by kubelet, not failed by the tool, so this
    bounds how large a single step's intermediates can get.

    The default matches what the pool already runs. Raising it recreates the node pool.
  EOT
}

variable "k8s_pipeline_disk_type" {
  type        = string
  default     = "pd-standard"
  description = <<-EOT
    Boot disk type for pipeline nodes: pd-standard (HDD), pd-balanced, or pd-ssd. Alignment is
    heavily I/O bound and Fusion streams the work dir through this disk, so the type is a throughput
    decision, not only a cost one.

    The default matches what the pool already runs. Changing it recreates the node pool.
  EOT
}

variable "k8s_pipeline_use_spot" {
  type        = bool
  default     = true
  description = "Whether the pipeline pool uses spot instances"
}

variable "k8s_pipeline_head_machine_type" {
  type        = string
  default     = "e2-standard-2"
  description = "Machine type for the bioaf-pipeline-head node pool. This pool runs Nextflow head/coordinator pods only -- they're cheap (low CPU/memory) but must survive Spot preemption for the full pipeline duration, so this pool is on-demand. e2-standard-2 has 2 dedicated vCPU / 8 GiB RAM, plenty for one head pod with room to scale."
}

variable "k8s_pipeline_head_max_nodes" {
  type        = number
  default     = 5
  description = "Maximum number of nodes in the bioaf-pipeline-head autoscaler. Min is 0 -- nodes are provisioned on demand when a head pod is scheduled, then reclaimed via the autoscaler when no head pods remain."
}

variable "k8s_interactive_machine_type" {
  type        = string
  default     = "e2-standard-8"
  description = "Machine type for the interactive node pool. Defaults to e2-standard-8 (8 vCPU / 32 GB): the e2 family is allocated against any compatible host generation so it almost never stocks out, while n2-standard-* in us-central1-a has repeatedly hit GCE-out-of-resources for fresh interactive pool scale-ups. The 8-vCPU / 32 GB shape is large enough that both Small (2/8) and Medium (4/16) notebook tiers schedule on the same node."
}

variable "k8s_interactive_max_nodes" {
  type        = number
  default     = 5
  description = "Maximum number of nodes in the interactive autoscaler"
}

variable "k8s_system_machine_type" {
  type        = string
  default     = "e2-standard-2"
  description = "Machine type for the always-on system node pool that hosts GKE addons (calico-typha, fluentbit, gmp-operator, gke-metadata-server, etc.). e2-standard-2 has 2 dedicated vCPU and 8 GiB RAM (~1.9 CPU / ~7 GiB allocatable) -- enough headroom that one node per zone absorbs the full addon DaemonSet set. Avoid shared-core burstable types (e2-small, e2-medium): Kubernetes only sees the 940m burstable max as allocatable on either, so the autoscaler packs them identically and pins to max=2 per zone. Still small enough that Nextflow process pods cannot fit and fall back to the pipelines pool."
}

variable "k8s_system_max_nodes" {
  type        = number
  default     = 2
  description = "Maximum number of nodes in the system pool autoscaler. Min is hardcoded at 1 -- the pool must always have a home for system addons."
}

variable "bioaf_bootstrap_sa_email" {
  type        = string
  default     = ""
  description = "Email of the bioaf-bootstrap SA. When set, attaches the bioaf-managed=true Resource Manager tag to the GKE cluster so bioaf-app's roles/container.admin tag-condition resolves."
}

variable "gke_default_pool_zone" {
  type        = string
  default     = ""
  description = "Zone selected by the pre-flight capacity probe for the throwaway default node pool. When set, the cluster's top-level node_locations is constrained to this single zone so cluster bootstrap is not blocked by a per-zone GCE stockout on the implicit e2-medium default pool. The real node pools (system/pipelines/interactive/pipeline_head) set their own node_locations and remain multi-zone. Empty default is backward-compatible: GKE falls back to placing the default pool in all zones of the region (today's behaviour)."
}
