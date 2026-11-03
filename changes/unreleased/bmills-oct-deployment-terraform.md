### Infrastructure deployment

- A deploy, teardown, or storage destroy that is interrupted no longer reports success or clears what identifies the resources. It ends with "The operation was interrupted before it finished. Check its status, then try again." The run stays `failed`, and no orphaned resources are recorded for it.
- When an apply, destroy, or foundation bootstrap stops early, its running `terraform apply` or `terraform destroy` process is stopped before the state lock or work directory is released. If it cannot be confirmed stopped, the lock is kept and the run is marked failed. This does not cover `terraform init`, `plan`, or `output`, which can still run on after an interruption.
- Cancelling the foundation bootstrap now stops its Terraform apply. An interrupted bootstrap no longer records the infrastructure as initialized without its state bucket. Partial bootstrap still cannot be resumed.
- Abandoning a run stops its Terraform apply or destroy before releasing the lock. If Terraform does not exit, the run is marked failed instead of cancelled and the lock is kept.
- A compute deploy with a region or zone override now also restores the defaults when it is cancelled or when no zone has capacity. An unexpected error can still leave the override in place, and a successful deploy still loses the cluster's location.

### Fixes

- Destroying storage now clears the reference and literature bucket names, so a later deploy cannot pick up stale ones.
- Syncing compute config on AWS now restores the IAM role ARNs, OIDC provider, Cluster Autoscaler role, and work-node networking outputs.
