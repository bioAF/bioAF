variable "region" {
  type        = string
  description = "AWS region for the EKS cluster and its node groups."
}

variable "org_slug" {
  type        = string
  description = "Organization slug used in resource naming (matches the GCP module)."
}

variable "stack_uid" {
  type        = string
  default     = "pending"
  description = "Short unique id appended to resource names so a redeploy after teardown does not collide. Defaults to 'pending' so terraform destroy works from state without the original value (mirrors the GCP module)."
}

variable "account_id" {
  type        = string
  default     = ""
  description = "AWS account id (used for tagging / cross-checks; S3 bucket ARNs do not need it)."
}

variable "app_role_arn" {
  type        = string
  default     = ""
  description = "ARN of the bioaf-app IAM role (the EC2 instance profile the backend runs as). Granted an EKS access entry with cluster-admin so the app can drive the cluster out-of-cluster via the ClusterAuth seam (the EKS analog of the GKE OAuth-token path). When empty, no access entry is created and an operator must wire cluster access manually."
}

variable "kubernetes_version" {
  type        = string
  default     = "1.31"
  description = "EKS control-plane Kubernetes version."
}

variable "vpc_cidr" {
  type        = string
  default     = "10.0.0.0/16"
  description = "CIDR for the dedicated VPC the cluster runs in."
}

# --- Node group sizing (the AWS analogs of the GCP k8s_* machine types) ---
#
# GCP -> AWS instance mapping keeps vCPU/RAM close so behavior matches:
#   n2-highmem-16 (16/128)  -> r5.4xlarge  (16/128)  pipelines (high-mem genomics)
#   e2-standard-8 (8/32)    -> m5.2xlarge  (8/32)    interactive (notebooks)
#   e2-standard-2 (2/8)     -> t3.large    (2/8)     system addons / pipeline head

variable "pipeline_instance_type" {
  type        = string
  default     = "r5.4xlarge"
  description = "Instance type for the pipelines node group (high-mem, Spot)."
}

variable "pipeline_max_nodes" {
  type        = number
  default     = 20
  description = "Max nodes in the pipelines autoscaler."
}

variable "pipeline_use_spot" {
  type        = bool
  default     = true
  description = "Whether the pipelines node group uses Spot capacity (task pods, cheap + preemptible)."
}

variable "pipeline_head_instance_type" {
  type        = string
  default     = "t3.large"
  description = "Instance type for the pipeline-head node group. On-demand: the Nextflow head pod must survive the whole run (Spot preemption would kill the workflow)."
}

variable "pipeline_head_max_nodes" {
  type        = number
  default     = 5
  description = "Max nodes in the pipeline-head autoscaler."
}

variable "interactive_instance_type" {
  type        = string
  default     = "m5.2xlarge"
  description = "Instance type for the interactive (notebook) node group. On-demand."
}

variable "interactive_max_nodes" {
  type        = number
  default     = 5
  description = "Max nodes in the interactive autoscaler."
}

variable "system_instance_type" {
  type        = string
  default     = "t3.large"
  description = "Instance type for the always-on system node group (CoreDNS, EBS CSI controller, etc.)."
}

variable "system_max_nodes" {
  type        = number
  default     = 2
  description = "Max nodes in the system node group (min is 1 so addons always have a home)."
}
