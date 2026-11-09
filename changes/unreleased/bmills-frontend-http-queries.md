### Sign-in and sessions

- Signing out, or a session expiring, now ends everything that session
  started: requests, retries, uploads, downloads, and background refreshes.
  A late answer from the previous session can no longer appear for, or sign
  in, the next user.
- A wrong password keeps you on the sign-in form with its message instead
  of reloading the page.
- Failed requests show the server's explanation (for rate limits, including
  the limit) instead of a bare error code.
- While bioAF finishes an upgrade, open pages say so and recover on their own
  when it is done.

### Lists, searches, and live status

- Searching projects, experiments, references, datasets, and the plot archive
  waits for a pause in typing, and an older search can no longer replace the
  results of the one you typed last. The file browser, audit log,
  activity feed, and other filtered lists always show the page and filters
  you chose.
- Pages that refresh on their own (pipeline runs and run detail, notebooks,
  work nodes, pipeline templates, cellxgene publications, reference imports,
  the glossary, validation studies, agent and QC AI reviews, notifications,
  infrastructure components, backups, service health, and deployment
  progress) check one request at a time and stop when you leave the page.
- When a refresh fails, the page keeps what it last showed and says it may be
  out of date, with Retry. A first load that fails says so instead of showing
  an empty list or an idle status. This includes literature search results,
  reference import progress, the glossary's AI scan and pending-review count,
  and the notebook and component image build status.

### Literature

- Stopping a watched search or AI literature review stays stopped when you
  start another. Clicking between searches always shows the results of the
  one you selected, and a watched search finishing does not take over the
  results you are reading.

### Downloads and upgrades

- Downloading a Terraform state file from Backups works: it is now sent with
  your session, which the server requires. Exports, the audit-log CSV, and run
  reports keep the server's file names and show its explanation on failure.
- Installing an update now follows only the new upgrade. A completed status
  left over from the previous upgrade is no longer reported as this one's
  outcome.
