### Installation metrics

- Add optional, opt-in installation metrics. Nothing is sent unless an admin chooses **Share installation
  metrics**. New installations make the choice on the setup wizard's Organization Name step, with neither
  option preselected. On existing installations, admins who can configure the installation are asked on
  entering the app until one of them saves a choice. Reports carry only a random installation ID, the UTC
  reporting date and release versions, for setup completion, first observation and successful upgrades. They go to PostHog (US Cloud)
  with IP addresses discarded and location lookup disabled. See
  [Installation Metrics](docs/installation-metrics.md) for the full field list, the 12-month retention, and
  how to request deletion through [privacy@bioaf.co](mailto:privacy@bioaf.co).
- Add **Settings > System Settings** to view, change or withdraw the choice and see the installation ID.
  Withdrawing cancels every report still waiting to be sent.
- `./bioaf update` records each update as an operation (ID, original start and source version, completion)
  in `host-state/deployments.json`, keeps it across retries of the same target, and finishes an interrupted
  update to the running target with its readiness checks instead of skipping it.
- Add `BIOAF_INSTALL_METRICS_DISABLED`, `BIOAF_POSTHOG_PROJECT_TOKEN` and `BIOAF_POSTHOG_HOST` host settings,
  and `./bioaf reset-install-metrics-identity --confirm` to give a deliberate copy of an installation its own
  identity.
