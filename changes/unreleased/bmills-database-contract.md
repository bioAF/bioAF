### Deployment

- Remove the unused Python deployment package (`cli/`, installed as `bioaf` with `deploy` and `destroy` commands) and the application Helm chart (`helm/bioaf/`) from the source tree. No installer, update path, or CI job used them. Supported management stays with the repository-root `./bioaf` script (`setup`, `update`, `backup`, `stop`) and the Infrastructure > Components page (compute Teardown, Destroy Storage). Older release tags still contain both for anyone reconstructing a historical install. Updating does not uninstall anything, delete cluster resources or Secrets, or change your database.

### Updates and the database

- Every database upgrade now makes a verified recovery backup first, on the host in `backups/` (`bioaf_pre_upgrade_<from>_to_<to>_<time>.dump` with a `.json` manifest). If the backup cannot be made, for example for lack of disk space, the update stops before changing anything and says why. Previously a failed backup was reported as a warning and the update carried on.
- The backend upgrades the database itself when a new version starts, so this protection also covers the first update run by an older `./bioaf`. Until the upgrade has completed it answers every request with an "upgrade in progress" message and does no background work; the web app shows that message, keeps any open page as it is, and resumes by itself.
- `./bioaf update` is no longer a no-op just because the new version's images are installed: if the database upgrade did not finish, running it again finishes it. Failures are recorded with the step and reason in `update-status/`, and the Settings page no longer reports an update as completed before the database upgrade has completed.
- `./bioaf migrate`, `setup` and `reset-db` use the same upgrade steps. `./bioaf backup` now checks the dump it wrote. The deployment guide has a recovery procedure.
- The backend's readiness check (`/api/health/ready`) answers 503 while an upgrade is in progress or has failed, and when the database does not answer (it used to answer 200 with an error body).
- Installs from before v0.15.0 whose experiments held the same sample ID more than once could not be updated at all. They now update: each of those samples gets its own ID (`<old ID>~<sample number>`) and keeps its old ID on record.

### Samples

- A sample whose ID was changed by that upgrade shows its old ID ("before upgrade") on the experiment's sample list and details, in `relabeled_from` on the sample APIs, and in the experiment export's sample manifest. Looking up a sample by such an old ID through the integration API answers 409 with the candidates (each sample's current ID, following any later rename) instead of picking one. No sample can take such an old ID again: creating, bulk-creating, renaming or claiming a sample with it answers 409 with the candidates, CSV and sheet imports skip that row with its reason, and accession imports and file ingest leave it out.
- Moving a sample to a different sequencing batch no longer fails with a server error when the new batch already has a sample in position 1.

### Fixes

- Records whose creation or update time is missing (possible only after changes made outside bioAF) no longer make roles, environments, custom pipelines, sample or sequencing batches, manifest entries or auto-run lists fail to load; the time shows as not set. These API fields can now be `null`.
- A notebook session's Recreate keeps its other input files when one recorded link has no file, and its provenance no longer lists a file of unknown direction as an output.
