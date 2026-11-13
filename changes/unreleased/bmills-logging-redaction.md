### Logging

- The backend now writes each log record as one JSON object on one line of stdout (`time`, `severity`,
  `logger`, `message`, and `exception`, `stack` or `fields` when present), in development as in
  production. Tracebacks and multi-line or hostile text stay inside their record. Records from
  background jobs that were previously dropped (Kubernetes connection, cluster quota, orphaned
  resources, run output location, and the work-directory and validation-fetch reapers) now appear.
- Each backend record is now ingested into Cloud Logging once. `BIOAF_LOG_DELIVERY` selects the one
  path: `stdout` (the default in both Compose files; the VM's Ops Agent ships the backend's output) or
  `direct` (the backend ships to Cloud Logging itself, for hosts where nothing collects container
  output). Before, GCE installs sent every backend record twice, through the agent and through an
  automatically attached direct handler. A manual install that relied on that automatic handler and
  runs no collector must now set `BIOAF_LOG_DELIVERY=direct`.
- Redaction now covers the live JWT signing key, the Slack client and signing secret slots, each
  encryption key and the database password, as well as the SMTP password, in messages, tracebacks
  (including the first one written) and structured fields. A record that cannot be formatted or
  sanitized is replaced by a fixed "log record withheld" line. Request logs no longer include
  headers. Organization credentials stored in the database, Uvicorn's own output, other containers
  and nginx's access log are not covered; see the deployment guide's Logging section.

### Updates and setup

- `./bioaf setup`, `start` and `update` (including retries, and updates started from an older
  release) install a new Ops Agent config before the services restart. It parses the backend's JSON
  lines, so severity, logger and fields become Cloud Logging fields. The previously shipped config is
  replaced automatically after the agent validates the new one; if the agent does not come back, the
  previous config is restored. An agent config you changed yourself is left alone and the command
  stops, saving the new config to `host-only/` for you to merge, or add `# bioaf-managed: operator`
  to keep managing it.
- In `direct` delivery these commands install no agent, and stop if an agent config would also ship
  container logs. A config you have checked can be marked with `# bioaf-log-delivery: no-container-logs`.
