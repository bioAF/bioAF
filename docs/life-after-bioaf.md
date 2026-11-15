# Life After bioAF

Research data and infrastructure records remain in standard formats. Preserve and verify what you need
before deleting the application host or storage. General file exports, GEO metadata packages, database
dumps and platform-config backups serve different purposes.

## Preserve the Records

| Asset | Where to obtain it | What to retain |
| --- | --- | --- |
| Research/application records | PostgreSQL and database backups | A verified database dump, source version and recovery instructions |
| Registered files and outputs | Configured GCS, S3 or NFS storage | Actual file contents, paths and recorded checksums |
| Notebooks and scripts | Synced session/output storage | `.ipynb`, `.Rmd`, scripts and registered output files |
| Environments and pipelines | Version definitions, source repositories and image registries | Code refs, Dockerfile/conda definitions, parameters and needed images |
| Infrastructure | Terraform state and cloud account | State, configuration and ownership/access information |
| Encrypted settings | Host configuration plus encrypted database fields | Required encryption keys, stored securely alongside recovery material |

New UI database snapshots carry creation-time checksums. Legacy snapshots can require explicit unverified
restore confirmation; test recovery before relying on one. Host `./bioaf backup` dumps and updater recovery
sets are distinct from UI snapshots. See [Backup & Recovery](user-guide-admin.md#backup--recovery).

## Copy Files

Use [Data Exports](guides/data-exports.md) for bounded experiment/project packages with actual file bytes.
Their 2 GiB and count/report limits make them unsuitable for moving an entire large installation at once.
GEO exports contain submission metadata and checksums, not the data files themselves.

For larger migrations, inventory the configured buckets/prefixes or NFS paths, then copy directly with
storage tools appropriate to the backend, such as `gcloud storage`, the AWS CLI or filesystem tools.
Use the actual recorded locations; bucket names and prefixes vary by installation. Verify checksums and
file counts after the copy. Include unregistered files and Work Node scratch that matter to you explicitly;
bioAF cannot export records it never registered or storage it never synced.

## Retire the Installation

1. Save and verify a database backup and its required keys/configuration.
2. Copy files, references, literature documents, synced notebooks and any required image/code artifacts.
3. Save Terraform state and a resource inventory so remaining resources can be managed independently.
4. On **Infrastructure > Components**, use **Teardown** to remove the managed compute stack. Storage buckets
   are retained by this action. **Destroy Storage** separately removes managed buckets and storage-module
   messaging resources, including GCP Pub/Sub topics/subscriptions; it can also remove AWS image-build
   infrastructure when deployed. Use it only after verifying external copies and saving required configuration.
5. Stop the application on its host with `./bioaf stop`. Delete the VM through the cloud provider after
   preserving host-only dumps/configuration and checking what remains billable.

See [Teardown](deployment-guide.md#teardown). Stopping containers alone does not remove cloud workloads,
volumes, registries or storage. Provider versioning and soft-delete policies can also retain bytes after
delete operations.
