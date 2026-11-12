### Fixes

- Pipeline run reviews are scoped to your organization. A run in another
  organization answers "not found", and a review's sample verdicts must name
  samples that belong to that run; any bad reference rejects the whole review
  before anything changes.
- The run review panel no longer fails when a review has no recorded reviewer.

### Developer

- The internal API schema is exported offline to
  `backend/contracts/internal-openapi.json`
  (`python -m app.cli.export_openapi`), and frontend transport types for user
  summaries, project detail, notebook sessions, the provenance graph and run
  reviews are generated from it (`npm run contracts:generate`). CI fails when
  either is out of date.
- Frontend shared types are split into domain modules under
  `frontend/src/lib/types/`; `@/lib/types` keeps every existing name.
