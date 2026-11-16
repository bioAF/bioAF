### Webhooks

- Webhooks now reach only public HTTPS receivers by default. Private, loopback, link-local and cloud metadata
  addresses are refused when a subscription is saved, tested, replayed and delivered, and the address is checked
  again at connection time. An operator can allow specific internal receivers per organization with
  `BIOAF_WEBHOOK_INTERNAL_DESTINATIONS`. A failed delivery records a short error code instead of the receiver's
  response.
- All eight webhook events now carry the documented fields, including the experiment's and sample's external IDs,
  and are sent only after the change is saved. An event from a change that was rolled back is never sent.
  Experiment and sample events now come from every place those records change, including bulk import, sample
  sheet writeback and the Integration API.
- `file.registered` and `file.ready` are now sent for uploads, pipeline outputs, fetched public data, notebook
  and session outputs, QC plots and the plot scanner. `file.ready` is sent once, when the stored file is confirmed
  complete (and its checksum verified, when one is expected).
- `./bioaf register-outputs` sends the same webhook events as the server and exits with an error, naming the
  affected files, when an event could not be recorded or an output matches more than one older file record.

### Files

- Files can carry an external ID (your LIMS or sequencing provider's identifier), set at upload or output
  registration and shown in the file details.
- When an expected MD5 is known (stated at upload, or listed in a fetchngs samplesheet), bioAF checks it against
  the stored file on S3, GCS and NFS in a background worker, retrying storage errors on a fixed schedule. File
  details show the expected and observed MD5, progress, and a failure reason with a Retry action. The uploader is
  notified when a verification fails ("File verification failed" notification setting).
- On NFS, a file being verified is first copied to a protected, read-only location that pipelines and uploads
  cannot change, and that copy becomes the file's stored location. Verification on NFS needs
  `BIOAF_NFS_PRODUCER_UIDS`; see the deployment guide.
- `BIOAF_FILE_VERIFICATION_CONCURRENCY` (1 to 8, default 1) sets how many verifications each backend runs at once.

### Validation studies

- A validation study waits for its fetched FASTQs to pass checksum verification before analysis, and stops with a
  clear error (not a "missing data" classification) when one fails. The analysis runs on exactly the verified
  inputs of the selected samples, never an unselected sample or an earlier download.

### Reproduction

- Reproducing a pipeline run uses the input files the original run recorded. Files linked to a sample afterward
  are ignored, and a recorded input that is no longer available is reported by file ID instead of replaced. With
  "drop samples without files" confirmed, a sample that lost any of its inputs (for example, one lane) is dropped
  whole. A run whose inputs were not recorded cannot be reproduced exactly.

### Fixes

- One organization can no longer claim another organization's unclaimed experiments or samples by ID.
- The notebook register-outputs endpoint accepts only the session's own storage location and only sessions of the
  caller's organization.
- The plot scanner files each image under the organization that owns its experiment or run.
- A notification addressed to a specific user reaches only an active user of the same organization.
