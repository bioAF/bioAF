### Fixes

- Notifications, activity feed entries and internal events now go out only
  after the change they describe has been saved. Before, an upload, work node
  launch or stop, notebook idle stop, pipeline completion or failure, run
  review, reference deprecation, budget or quota alert, environment build,
  auto-run budget stop, validation study error or ingest notice could be
  announced before it was saved, so a link in the notification could open an
  out-of-date page, and a change that failed to save could still notify people.
- An environment rebuild no longer creates cascade pipeline versions before
  the rebuilt environment is saved as ready.
- When a notebook launch or headless notebook run is refused for exceeding a
  compute quota, the refusal is now kept in the audit log alongside the quota
  warning, as pipeline launches already did.
