### Fixes

- Fix BigQuery billing export verification failing with a 403
  (`bigquery.tables.list denied`). The dataset read grant was landing on the
  project's default compute service account instead of the service account the
  backend actually queries as (`bioaf-app`), so verification and cost sync could
  never read the dataset. The grant now targets the runtime service account, and
  clicking **Verify** on an already-affected install self-heals the permissions
  by re-applying the billing export module, then completes: no manual `gcloud` or
  `bq` commands required.
