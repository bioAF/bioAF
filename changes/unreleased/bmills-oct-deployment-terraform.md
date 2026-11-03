### Infrastructure deployment

- A deploy, teardown, or storage destroy that is interrupted no longer reports success or clears what identifies the resources. It ends with "The operation was interrupted before it finished. Check its status, then try again." The run stays `failed`, and no orphaned resources are recorded for it.
- Terraform is stopped on every way out of an operation, and its state lock and work directory are released only after it has exited. If it cannot be confirmed stopped, the lock is kept and the run is marked failed, so another operation cannot run over it.
- Cancelling the foundation bootstrap now stops its Terraform process. Partial bootstrap still cannot be resumed.
- Abandoning a run stops Terraform before releasing the lock. If Terraform does not exit, the run is marked failed instead of cancelled and the lock is kept.
- A compute deploy with a region or zone override restores the defaults on every exit, including when no zone has capacity. This does not fix a deployed cluster's location being lost after a successful deploy.
- The stack deploy progress bar now counts the AWS image build, so its totals match what is being deployed.

### Fixes

- Destroying storage now clears the reference and literature bucket names, so a later deploy cannot pick up stale ones.
- Syncing compute config on AWS now restores the IAM role ARNs, OIDC provider, Cluster Autoscaler role, and work-node networking outputs.
