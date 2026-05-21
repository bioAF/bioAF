### Settings

- Notebook settings and work-node settings are now a single **Workbench
  Settings** page (Settings > Workbench Settings) with separate Work Nodes and
  Notebooks cards.
- Slack configuration now lives only in Settings > Integrations (Slack tab). The
  duplicate standalone Slack settings page was removed and its URL redirects
  there.

### Fixes

- The Job Browser and Quotas buttons on Infrastructure > Compute work again.
  They were previously caught by redirects that bounced back to the cluster page.

### Removed

- Cleaned up duplicate and unreachable pages left over from earlier navigation
  changes (old Results, Data, Pipelines, References, and Compute landing pages,
  the standalone component catalog, the ingest dashboard, and the global pipeline
  triggers page). Their old URLs redirect to the current locations. Automated
  pipeline runs are configured per experiment, from the experiment's Pipeline
  Runs tab.
