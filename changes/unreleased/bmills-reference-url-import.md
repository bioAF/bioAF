### Reference data

- Reference data import-from-URL works end to end. The endpoint previously
  500'd on every call because the backend tried to load a kubeconfig that
  doesn't exist in the VM-resident container, and the importer image and
  service account it referenced had never been built. The import now
  runs as an in-process asyncio background task in the backend: the
  HTTP request returns immediately after creating the dataset and
  progress rows, and the task streams the source URL straight into GCS
  via the existing `UploadService` credentials, optionally verifies an
  upstream `.md5` file, and optionally extracts gzip / tar / tar.gz
  archives. Progress is written directly to `ReferenceImportProgress`
  per chunk so the Import Status modal updates in real time. There is
  no GKE Job, no importer image, no internal callback token, no
  separate KSA -- the worker code is the running backend code, so a
  dev / branch build never desynchronizes from the worker the way a
  Pod-based design would.

- A backend restart mid-import leaves the row in whichever state was last
  reported (typically `downloading`) and does not resume; the user
  cancels the stuck import (existing `Cancel` button on the Import
  Status modal) and retries.
