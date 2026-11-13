### Configuration

- `BIOAF_COMPUTE_MODE` is now required by the backend and has one meaning
  everywhere. `local` simulates the compute, notebook, storage and work-node
  adapters; `kubernetes` (or the alias `k8s`) contacts their real backends;
  `slurm` is still accepted as a legacy value. Case and surrounding spaces no
  longer matter, so `K8S` or ` Kubernetes ` work. `LOCAL` or a padded `local`
  now selects simulation; before, those spellings silently selected real
  execution. Empty or unknown values stop the backend with a message naming
  the setting and the accepted values, instead of selecting real execution.
- Installs using the bundled `docker/docker-compose.yml` need no change: an
  unset or empty mode still means real backends (now spelled `kubernetes`).
  Manual development, custom launchers, and Alembic or CLI commands outside
  Compose must set the mode, for example `BIOAF_COMPUTE_MODE=local`. The
  development Compose file and `.env.example` now set `local`.
- The backend logs its effective configuration once at startup:
  `Compute configuration: mode=... simulation=... active_compute_stack=...`.
- Setting `BIOAF_COMPUTE_MODE=slurm` alone no longer starts SLURM job-status
  polling. Polling runs only when the initialized compute stack is SLURM and
  the mode is not `local`. SLURM itself remains unavailable.

### Updates

- The backend checks its settings before upgrading the database. Rejected
  settings stop the container with a configuration message, rather than
  starting in maintenance with database-retry advice.
- `./bioaf update` checks the new release's settings before switching to it.
  A rejected check leaves the current release and its pinned image tag
  running, records the "Checking backend configuration" step as failed, and
  asks you to correct `docker/.env` and run the same update again. The update
  page shows the new step.
- Retrying an update to a release that is already installed now applies the
  current `docker/.env` to the services before upgrading the database, so an
  update that stopped on a bad setting recovers once the setting is corrected.
- `./bioaf migrate` checks settings first and, on a configuration problem,
  says so and explains how to recreate the backend with the corrected value.
