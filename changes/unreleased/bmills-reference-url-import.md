### Reference data

- Reference data import-from-URL works end to end. The endpoint previously
  500'd on every call because the backend tried to load a kubeconfig that
  doesn't exist in the VM-resident container, and the importer image and
  service account it referenced had never been built. The submission path
  now authenticates to bioaf-cluster via the same out-of-cluster client
  the compute and notebook adapters use, the Pod runs the bioAF backend
  image with `python -m app.workers.reference_importer` as its
  entrypoint, and it reuses the existing `bioaf-pipeline-runner` KSA for
  GCS access via Workload Identity. The Pod streams the source URL
  straight to GCS, optionally verifies an upstream `.md5` file, and
  optionally extracts gzip / tar / tar.gz archives. It POSTs progress
  back to `/api/internal/references/{id}/import-progress` so the Import
  Status modal updates in real time without holding the request open.

- The importer Pod's callback URL is now derived from the existing
  Networking settings (`networking_hostname`, `networking_domain`,
  `networking_https_enforced`) that operators already configure for the
  UI / API to be reachable; no separate platform_config key. The
  endpoint returns 503 with a clear remediation message if Networking
  has not been configured yet, instead of failing late inside the Pod.

- The internal callback token the importer Pod uses to authenticate
  back to the bioAF API is now bootstrapped automatically on the first
  URL import. A random 256-bit token is generated, stored in
  platform_config as `internal_callback_token`, and reused for every
  subsequent import. The `BIOAF_INTERNAL_TOKEN` env var still overrides
  it when set, but is no longer required for the feature to work.
