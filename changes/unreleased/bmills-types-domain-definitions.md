### Fixes

- Pipeline run reviews are scoped to your organization. A run in another
  organization, or one that does not exist, answers "not found", and a review's
  sample verdicts must name samples that belong to that run. Any bad, malformed
  or out-of-range sample reference, or more sample verdicts than the run has
  samples, rejects the whole review before anything changes.
- The run review panel no longer fails when a review has no recorded reviewer.

### Developer

- The internal API schema is exported offline to
  `backend/contracts/internal-openapi.json`
  (`python -m app.cli.export_openapi`), and frontend transport types for user
  summaries, project detail, notebook sessions, the provenance graph and run
  reviews are generated from it (`npm run contracts:generate`). CI fails when
  either is out of date. The export ignores inherited `BIOAF_*` settings and
  `.env` files, so it needs no configuration.
- Frontend shared types are split into domain modules under
  `frontend/src/lib/types/`; `@/lib/types` keeps every existing name.
- Three shared backend helper modules are held to a strict per-module mypy
  gate.
