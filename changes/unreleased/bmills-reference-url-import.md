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

- The reference detail page now shows in-flight import progress and a
  `Cancel import` button when the dataset is in the `uploading` state,
  and an error banner with a `Delete` button when the dataset is in
  the `failed` state. Previously these controls were only on the
  import wizard, so navigating away during a long download left no way
  to see progress, cancel, or clean up a stuck or failed dataset.

- Force GCS uploads to a resumable / chunked upload with an 8 MiB
  chunk size on every blob the importer writes. Without this the
  google-cloud-storage SDK tries to buffer the entire body in memory,
  which back-pressures the URL stream and silently stalls multi-GB
  imports well before they finish (e.g., a 10 GB 10x Genomics
  reference would freeze around 56 MB). With chunked uploads, each
  8 MiB block is PUT to GCS as soon as it has been read.

- Merge "Add Reference" and "Import from URL" into a single
  "Add Reference Data" button. The unified page has an Upload / URL
  Import toggle at the top and renders the matching form. The
  "Upload new version" button on the reference detail page now
  deep-links into the same page with the Upload side preselected.

- The version field on both forms is now optional and auto-populated.
  When the user enters or selects a name + category, the page fetches
  the existing versions for that name and prefills the field with the
  next `v<N>` (or `v1` if this is the first version). Users keeping
  their own naming convention (e.g., `GRCh38.p14`) can still type any
  value to override.

- The URL Import form now auto-selects the extract mode from the
  source URL's file extension: `.tar.gz` / `.tgz` -> `tar.gz`,
  `.tar` -> `tar`, `.gz` -> `gzip`, anything else -> `none`. As soon
  as the user changes the extract dropdown themselves, later URL edits
  no longer overwrite their selection.
