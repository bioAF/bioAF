# Foundation bucket public-access prevention (existing GCP installations)

Fresh installations get public-access prevention on every bioAF bucket from Terraform. Existing
installations get it on all nine buckets through the normal update, with no commands to run:

- **The seven application buckets** change in place through **Infrastructure > Components >
  Check for Infrastructure Updates**.
- **The two foundation buckets** (Terraform state and persistent backups) are applied by the
  backend itself when it starts on the new release. Bootstrap created them with temporary local
  state, so Terraform has no record of them, and bioAF refuses a foundation plan that would create
  them again.

## What the backend does at startup

On a GCP installation that has been initialized, the backend reads the two bucket names recorded
at bootstrap (`terraform_state_bucket`, `backups_bucket_name`) and, for each:

1. refuses the name unless it starts with `bioaf-` (the app service account's storage grant is
   limited to those buckets);
2. reads the bucket; if public-access prevention is already `enforced`, it changes nothing;
3. otherwise sets only `public_access_prevention` to `enforced`, on the condition that the bucket
   has not changed since it was read (metageneration match);
4. reads the bucket back and reports success only if the setting is `enforced`.

It changes no other setting, imports no state, and does not rerun bootstrap. One bucket's failure
does not stop the other. Nothing happens on AWS, before initialization, or in simulated mode.

Each outcome is logged by the backend (logger `bioaf.services.foundation_bucket_guard`):

- `Foundation bucket NAME public-access prevention: enforced` (or `already_enforced`);
- `Public-access prevention not enforced on foundation bucket NAME: ...` with the reason.

A failure is retried on the next backend start. It does not stop the backend from starting.

## Checking it

Look for the two log lines above after the update. Enforcement is a tightening only: bioAF never
grants `allUsers` or `allAuthenticatedUsers`, signed uploads and downloads keep working, Terraform
state reads and locking are unaffected, and backups keep writing to the backups bucket.
