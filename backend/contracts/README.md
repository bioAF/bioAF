# API contracts

`internal-openapi.json` is the main app's OpenAPI schema, exported offline. It is
the authority for the generated frontend transport types in
`frontend/contracts/internal-api.ts`. It is not served: production answers 404
for the main app's docs and schema. The Integration API (`/api/v1/integrations`)
keeps its own public schema and is not part of this file.

## Commands

| Step | Command (directory) |
| --- | --- |
| Export the schema after an API change | `python -m app.cli.export_openapi` (`backend/`) |
| Regenerate frontend types from it | `npm run contracts:generate` (`frontend/`) |
| Check both without writing (CI) | `python -m app.cli.export_openapi --check`, `npm run contracts:check` |

Commit the regenerated files with the change that caused them. Never edit either
file by hand.

## What the schema does and does not prove

- The export normalizes only `info.version` (the release number) and sorts JSON
  object keys. Paths, required fields, nullability, defaults and enums are kept.
- `tests/test_internal_schema_pilot_responses.py` checks real responses against
  the schema for the generated families. Other responses are not checked.
- A `string` in the schema says nothing about which values a validator accepts;
  open strings stay open in the generated types. Unconstrained JSON
  (`metadata`, `sample_verdicts_json`) is typed as unknown data.
- Generated types do not validate network data at runtime.
