# ADR-007: UI-Driven Terraform Execution for Infrastructure Management

**Status:** Accepted (implementation superseded by [ADR-066](ADR-066-terraform-single-execution-owner.md))
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

> **Note (2026-06-09):** The UI-driven principle below stands. The original
> implementation (a single root Terraform config toggled by `enable_<component>`
> tfvars, applied synchronously by `TerraformService` and committed to a GitOps repo)
> is superseded by the per-module `TerraformExecutor`. See
> [ADR-066](ADR-066-terraform-single-execution-owner.md).

## Context

bioAF manages significant GCP infrastructure on behalf of users who are not infrastructure engineers. This infrastructure needs to be provisioned, modified, and decommissioned throughout the platform's lifecycle. Options for how users interact with this infrastructure:

1. **Users edit Terraform directly:** Too technical for our target audience. Defeats the purpose of bioAF.
2. **bioAF generates and runs Terraform behind the scenes, invisible to the user:** Convenient but opaque. Users can't understand what's happening or verify changes.
3. **bioAF generates Terraform and presents plans for user review before applying:** Transparent, auditable, and gives users control while hiding complexity.

## Decision

The bioAF UI is the primary interface for all infrastructure changes. When a user enables, disables, or modifies any component:

1. bioAF updates parameterized Terraform templates on the backend (populating `terraform.tfvars` with user selections)
2. bioAF generates a Terraform plan
3. The UI presents a human-readable summary of the plan: resources to be created/modified/destroyed, estimated cost impact, expected provisioning time
4. User reviews and confirms
5. bioAF applies the plan with real-time progress in the UI
6. Updated Terraform files and state are committed to the GitOps repo
7. The change is recorded in the audit log

### Terraform Module Structure

Each optional component is a separate `.tf` file with a feature flag variable:

```hcl
# slurm.tf
variable "enable_slurm" {
  type    = bool
  default = false
}

resource "google_compute_instance" "slurm_controller" {
  count = var.enable_slurm ? 1 : 0
  # ...
}
```

`terraform.tfvars` is the single file that the UI modifies:

```hcl
enable_slurm      = true
enable_jupyter     = true
enable_rstudio     = false
enable_nextflow    = true
enable_cellxgene   = false
slurm_max_nodes    = 20
slurm_instance_type = "n2-highmem-8"
slurm_use_spot     = true
# ...
```

### Terraform State

- Stored in a GCS backend bucket in the customer's project (versioned)
- bioAF's FastAPI backend runs Terraform via subprocess with appropriate credentials
- State locking via GCS to prevent concurrent applies
- The customer owns their Terraform state: it's in their GCP project

## Rationale

- **Transparency without complexity.** Users see what will change and what it will cost without needing to understand HCL. The "review plan before apply" pattern is familiar from cloud consoles.
- **Auditability.** Every infrastructure change has a corresponding git commit in the GitOps repo and an audit log entry. This satisfies the traceability requirement.
- **Rollback.** Since every state is a git commit and Terraform state is versioned, rolling back to a previous configuration is straightforward.
- **Terraform is the right tool.** It's the industry standard for GCP infrastructure, the Google HPC Toolkit is Terraform-native, and the declarative model makes it safe to re-apply (idempotent).
- **Parameterized templates reduce risk.** The UI only modifies variable values, not HCL structure. The templates are tested and version-controlled as part of the bioAF release.

## Consequences

- The bioAF FastAPI backend must be able to execute `terraform plan` and `terraform apply` as subprocesses. This requires Terraform to be installed in the control plane container.
- The backend must parse Terraform plan output and generate human-readable summaries for the UI. Terraform's JSON plan output format (`terraform show -json`) can be parsed for this.
- Concurrent infrastructure changes must be prevented (only one apply at a time). The UI should show "infrastructure change in progress" and queue or block additional changes.
- Cost estimation requires mapping Terraform resources to GCP pricing. This can be done via the GCP Pricing API or a static pricing table updated with each bioAF release.
- The GitOps repo must be kept in sync with actual Terraform state. The control plane is the only writer: manual edits to the GitOps repo's Terraform files are not supported in v1 (they'd be overwritten on next UI-driven change).
