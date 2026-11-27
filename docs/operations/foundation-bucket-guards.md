# Foundation bucket public-access prevention (existing GCP installations)

Fresh installations get public-access prevention on every bioAF bucket from Terraform. Existing
installations get it on the seven application buckets through the normal storage plan and apply.
The two foundation buckets (Terraform state and persistent backups) are different: bootstrap
created them with temporary local state, so Terraform has no record of them. A foundation plan
would try to create them again, and bioAF refuses that plan. An operator applies this one setting
to each of those two buckets by hand instead.

This procedure changes only public-access prevention. It does not import state, rerun bootstrap,
reset initialization or touch any other bucket setting.

## Who runs it

An operator with `storage.buckets.get`, `storage.buckets.getIamPolicy` and `storage.buckets.update`
on the installation's project, working from a shell with `gcloud` authenticated as that operator.
Run each step yourself and read its output before the next one.

## 1. Read the recorded bucket names

On the bioAF VM, from the checkout:

```sh
docker compose -f docker/docker-compose.yml --env-file docker/.env exec -T db \
  psql -U bioaf -d bioaf -tAc \
  "SELECT key, value FROM platform_config
   WHERE key IN ('gcp_project_id', 'terraform_state_bucket', 'backups_bucket_name')"
```

Stop if `terraform_state_bucket` or `backups_bucket_name` is missing or empty. Do not work out a
name from the naming convention; reconcile the recorded configuration first. Stop if a recorded
bucket does not exist in the recorded project (step 2 fails with "not found").

## 2. Record the current state of each bucket

For each recorded name (`STATE_BUCKET`, `BACKUPS_BUCKET`):

```sh
gcloud storage buckets describe gs://STATE_BUCKET --project=PROJECT_ID --format=json > state-before.json
gcloud storage buckets get-iam-policy gs://STATE_BUCKET --project=PROJECT_ID --format=json > state-iam-before.json
```

Check before continuing:

- `project_number` belongs to the recorded project, and `creation_time` is the installation's
  bootstrap date. If the bucket does not look like this installation's bucket, stop.
- Note `public_access_prevention`, `uniform_bucket_level_access`, versioning and lifecycle.
- Look for `allUsers` or `allAuthenticatedUsers` in the IAM policy. bioAF never grants them. If
  either is present, find out why before continuing: enforcement makes GCS ignore those grants.

Keep the four files with the change record.

## 3. Check that nothing else is running

Open **Infrastructure > Components** in bioAF and confirm no infrastructure operation is in
progress, or ask the backend directly:

```sh
curl -s -H "Authorization: Bearer ADMIN_TOKEN" https://BIOAF_HOST/api/v1/infrastructure/operations/active
```

Do not start while a deploy, update, teardown or backup is running.

## 4. Apply the setting

```sh
gcloud storage buckets update gs://STATE_BUCKET --public-access-prevention
gcloud storage buckets update gs://BACKUPS_BUCKET --public-access-prevention
```

Each command is independent. If one fails, the other bucket keeps its new guard; fix the cause and
rerun only the failed command. Rerunning a command on a bucket that is already enforced is harmless.

## 5. Confirm

For each bucket:

```sh
gcloud storage buckets describe gs://STATE_BUCKET --project=PROJECT_ID --format=json > state-after.json
diff state-before.json state-after.json
```

The only differences should be `public_access_prevention: enforced` and `update_time`. Name,
project, creation time, uniform access, versioning and lifecycle must be unchanged. Compare the
IAM policy the same way.

Then confirm the buckets still do their jobs:

- **State bucket:** `gcloud storage ls gs://STATE_BUCKET/` still lists the module state, and the
  next infrastructure plan or update reads state and takes its lock without error.
- **Backups bucket:** start a backup from **Infrastructure > Backup & Recovery** and confirm it
  completes and appears in the backup list.

Record, per bucket, whether the change succeeded. The installation is guarded only when both are.
