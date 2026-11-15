### Installation metrics

- Add optional, opt-in installation metrics. The setup wizard's Organization Name step and, for existing
  installations, a one-time prompt for admins ask whether to share them; nothing is sent unless an admin
  chooses **Share installation metrics**. Reports carry only a random installation ID, the UTC reporting
  date and release versions, for setup completion, first observation and successful upgrades. See
  [Installation Metrics](docs/installation-metrics.md).
- Add **Settings > System Settings** to view, change or withdraw the choice and see the installation ID.
- `./bioaf update` records each update as an operation (ID, original start and source version, completion)
  in `host-state/deployments.json`, keeps it across retries of the same target, and finishes an interrupted
  update to the running target with its readiness checks instead of skipping it.
- Add `BIOAF_INSTALL_METRICS_DISABLED`, `BIOAF_POSTHOG_PROJECT_TOKEN` and `BIOAF_POSTHOG_HOST` host settings,
  and `./bioaf reset-install-metrics-identity` for a deliberate copy of an installation.
