### Fixes

- Pipeline run logs no longer display as a raw escaped blob (`b"...\n..."`) on
  in-progress runs. Logs now render as clean, line-wrapped output regardless of the
  Kubernetes client version in use.

### Behind the scenes

- Structural, behavior-preserving changes to how bioAF integrates with its cloud
  provider (storage, Kubernetes, container image builds, and credentials). These
  changes are invisible to end users and do not alter current behavior. They improve
  reliability and prepare bioAF for upcoming AWS-based deployment options alongside GCP.
