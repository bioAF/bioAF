### Database restore

- A restore review can no longer be accepted once its hour has run out. Before,
  a late Accept could replace the database with the expired review copy and
  lose everything written after the review ended. The Accept button stays, and
  a late Accept explains that the original database was kept.
- When the review times out, bioAF now finishes returning to the original
  database, removes the review copy, and lets a new restore start.
- While a review is open, background work pauses, and starting pipeline runs,
  deployments, notebook or cellxgene sessions, work nodes, image builds,
  imports, AI reviews, and glossary scans is refused with a message to accept
  or reject the review first. Browsing and the review controls keep working.
- A restore that cannot pause background work in time (for example, during a
  deployment) is refused before switching databases.
- If accepting or rejecting stops partway through renaming databases, bioAF
  keeps the review open and work paused, and says that an administrator must
  repair the databases, instead of reporting success.

### Auto-ingest

- Turning auto-ingest off now stops the listener, and turning it on twice
  quickly starts only one.
- A file event that fails to record no longer causes every later event to
  fail until a restart.

### Background jobs and restarts

- Starting an AI review, a glossary scan, or a lab-document import from a URL
  returns right away; the work continues in the background.
- After a restart, literature searches, AI literature reviews, lab-document URL
  imports, and PDF extractions that were cut off are marked failed, so they can
  be started again, instead of showing as in progress forever.
- Stopping a literature search stops all of its source queries.
- Storage statistics and billing sync no longer skip the remaining
  organizations after one organization fails.

### Operations

- The backend shuts down within a fixed time budget: requests get 10 seconds,
  then background work gets up to 35 seconds to finish or be stopped. The
  backend container's stop grace is now 60 seconds in both Compose files.
