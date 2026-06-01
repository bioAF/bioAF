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

- New `bioaf_api_url` platform_config key. The installer must set this to
  the publicly reachable bioAF API base URL (e.g.
  `https://bioaf.example.com`); the backend uses it to render the
  internal callback URL the importer Pod posts to. The endpoint returns
  503 with a clear remediation message if the URL or `BIOAF_INTERNAL_TOKEN`
  is not configured, instead of failing late inside the Pod.
