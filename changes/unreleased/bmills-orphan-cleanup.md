### Maintenance

- Removed dead and superseded code paths with no change to app behavior: the
  legacy pre-ADR-033 package-management API (`/api/packages`) and environment
  reconciler, an unused budget pre-flight service, two orphaned manifest-ingest
  helper services, and two unused frontend components (`LoadingState`,
  `SuperSeriesExportModal`). Compute-environment package changes continue to be
  made by editing the environment's Dockerfile/conda definition and creating a
  new version.
