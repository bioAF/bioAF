### Files and uploads

- Two files with the same name uploaded into one experiment are now stored separately. Before, the second
  upload replaced the first one's bytes while both files stayed listed. Each file filed into an experiment
  is stored under its own permanent file ID (`experiments/<experiment>/files/<file ID>/<file name>`, or
  `unlinked/files/<file ID>/<file name>` after **Unlink**), and keeps its original name everywhere in bioAF.
  This applies to browser uploads, direct uploads, automatic filing retries, **Retry now**, and to
  assigning, reassigning and unlinking existing files. Files stored before this release keep their paths
  until they are moved. An upload without an experiment stays at its own upload path until it is assigned.
- Filing and moving a file never overwrites anything already stored, and never claims an object it cannot
  prove is its own. If something unexpected is already where the file would go, the upload stays
  "Received, not yet filed" (with **Retry now** and **Cancel upload**) or the move is refused, and nothing is
  deleted. The original copy is removed only after the file's new location is saved; if that cleanup
  cannot finish right away, bioAF retries it in the background.
- A file name containing `/`, `\`, control characters or more than 255 bytes, or named `.` or `..`, is
  refused before anything is uploaded.
- **Cancel upload** for an upload that was already received now shows it as stopping until its stored
  copies are removed, then cancelled; it never removes a copy that belongs to another file. An upload
  cancelled while its bytes are still arriving is no longer saved as a file when the transfer finishes. On
  S3 buckets with versioning, a cancellation finishes only once no version of the upload is readable.
- On NFS, the copy a verified file's protected copy replaces is removed (a pipeline's own output is kept),
  so moving a verified file leaves exactly one copy.
- Moving a file whose checksum was verified checks it again at its new location instead of carrying the
  old result over. A file whose old copy is still waiting to be removed is fully removed when deleted.
- Assigning or unlinking a file through `/api/v1/files/{id}/assign` and `/api/v1/files/{id}/unlink` is now
  limited to the caller's own organization's files and experiments. A file whose stored copy other file
  records also use is not moved.
- Operators can find files from earlier releases that already share one stored object with a read-only
  query; see "Check for shared file storage references" in the deployment guide. bioAF does not change
  those records.

### Setup

- An administrator who closed the browser, or returns on another device, before setup finished can sign in
  and finish it. The sign-in page no longer redirects in a loop to setup and back. After signing in,
  **Finish setting up bioAF** offers **Review saved setup**, which opens the saved settings from the
  organization step. Continuing with unchanged settings does not save or validate them again; saved cloud
  credentials that were never validated are validated without being saved again; SMTP keeps its saved
  encryption and password.
- An infrastructure deployment that was already started is shown instead of being created again: either
  **Deployment already in progress** with **Continue to components**, or **The last deployment didn't
  finish** with **View details** and **Retry deployment**. Retry is available only with deploy permission,
  when no other infrastructure action is running, and after any required recovery is recorded; it asks
  **Continue infrastructure setup?** first. If bioAF cannot read whether a deployment already exists, it
  does not offer to create one.
- Components that are already enabled stay selected during setup review and are not queued again.
  Components added after a failed completion are still queued when you try again.
  Selecting a component that is already enabled, from any screen, no longer restarts it.
- If setup cannot be marked complete, setup says "Setup couldn't be marked complete. Your settings are
  saved." with **Try again**, instead of moving on as if it had worked.
- A signed-in user without setup permission sees "Setup isn't finished yet" with **Sign out** instead of
  the setup wizard. Opening any other page while setup is unfinished goes to setup.
- Setup's organization name, SMTP settings and completion now act on the signed-in administrator's own
  organization.

### API

- `GET /api/bootstrap/org-settings` returns the saved organization name to callers with infrastructure
  configure permission.
- `GET /api/v1/infrastructure/operations` accepts `action` (for example `action=compute_deploy`) to list
  only that kind of operation.
