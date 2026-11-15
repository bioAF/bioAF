# Development Guide

Read [Contributing](../CONTRIBUTING.md) and the [AI Guides](../ai_guides/README.md) first. Use Python 3.12,
PostgreSQL 16 and Node 20, matching the repository's CI and container builds. Backend tooling uses uv 0.13.5.

## Start the Development Stack

From the repository root:

```bash
docker compose -f docker/docker-compose.dev.yml up --build
```

Services are `backend`, `frontend` and `postgres`. Ports are 8000, 3000 and 5432 respectively. Development
Compose supplies its own credentials, encryption key and explicit local compute mode; do not reuse these
values in production. Its backend and frontend bind-mount application source.

## Manual Backend

Start only PostgreSQL if it is not already running:

```bash
docker compose -f docker/docker-compose.dev.yml up -d postgres
cp -n .env.example backend/.env
cd backend
uv sync --locked --python 3.12
```

This manual backend connects to the same PostgreSQL database and `pgdata` volume as the development
Compose stack. Use the same encryption key as that stack, not a newly generated key. Add the committed
development-only value from [docker-compose.dev.yml](../docker/docker-compose.dev.yml) to `backend/.env`:

```dotenv
BIOAF_ENCRYPTION_KEYS=yQWeSjhut-D91YUcqvDUfQ62wQHNq1G3vUstCSJpk9U=
```

The copied `.env.example` does not include this key. If your development stack uses a different keyring,
reuse that exact keyring instead. A fresh key cannot decrypt records the Compose backend already wrote.
This public development key must not be used in production. From `backend/`, run:

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --no-access-log
```

Run the initial commands from the repository root through the `cd` step, then remain in `backend/`. The copied
example points at the development database and sets `BIOAF_COMPUTE_MODE=local`. Settings read the environment
and the `.env` in the launch directory; an existing backend `.env` should be reviewed rather than overwritten.
For an existing configuration, set the mode explicitly in that file or in the shell.

The application and Alembic need the same settings. Local mode simulates only mode-aware providers;
cellxgene and other external integrations are not made offline. See [Configuration](configuration.md).

## Manual Frontend

In a separate terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev
```

For local API access, configure `NEXT_PUBLIC_API_URL` as needed for your backend; development Compose uses
`http://localhost:8000`. [Backend dependency instructions](../backend/DEPENDENCIES.md) and
[frontend dependency instructions](../frontend/DEPENDENCIES.md) explain lock updates and security floors.

## Backend Tests

Use a disposable PostgreSQL server with permission to create databases and roles. Some tests build
migration schemas, exercise restores or inspect historical releases. Do not point them at an application
or shared research database. A separate local container avoids reusing the development volume:

```bash
docker run --rm -d --name bioaf-test-postgres -p 55432:5432 \
  -e POSTGRES_USER=bioaf_test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=bioaf_test postgres:16
```

Wait for PostgreSQL readiness, then from `backend/`:

```bash
export BIOAF_TEST_DATABASE_URL=postgresql+asyncpg://bioaf_test:test@localhost:55432/bioaf_test
uv run pytest -n 4 --tb=short tests/
```

Stop the disposable server afterward with `docker stop bioaf-test-postgres`. Fixtures isolate mutable
module state and route sessions into per-invocation/per-worker schemas. Some test families use a
migration-built worker baseline and reset or roll back changes; others still use the existing schema
fixture. Schema fidelity is not universal. Historical upgrade tests need the repository's release tags
and full Git history.

For coverage, use `--cov=app --cov-report=term-missing --cov-report=json`. The separate unit selection is
`uv run python -m tests.support.unit_selection -q -n 0`; it is reported separately from the combined suite.

## Frontend and Other Checks

From `frontend/`:

```bash
npm run lint
npm run contracts:check
npx tsc --noEmit
npm test -- --runInBand
npm run build
```

From `backend/`:

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy . --ignore-missing-imports
uv run python -m app.cli.export_openapi --check
```

The standalone schema exporter isolates itself with synthetic settings; it does not need a live database
or your application credentials. Other backend CLI/Alembic commands still require valid settings. See
[API contracts](../backend/contracts/README.md) for the export/regenerate sequence after an API change.
Commit generated artifacts with their source change; never edit them by hand.

CI also builds and tests the installed Python/R SDK packages, runs Bats shell and update-agent/systemd
checks, validates GCP/AWS Terraform modules, scans dependencies/secrets, and lints Markdown. Follow the
commands in [ci.yml](../.github/workflows/ci.yml) for those environments. Terraform formatting/validation
checks do not prove a real cloud deployment.

Coverage is gated separately for backend lines/branches and frontend lines/branches by
[coverage_policy.json](../scripts/ci/coverage_policy.json). The floors are 83/69% for the backend and
75/77% for the frontend. The aggregate **Coverage Report** also checks required job outcomes; a partial
or failed run must not be presented as passing coverage.

## Shared Implementation Contracts

- Use the shared frontend API client and resource hooks for session ownership, cancellation, request state
  and polling. Use the shared experiment picker for server name search and bounded pages; the internal
  experiment list accepts page sizes 1-100 and excludes deleted experiments by default.
- Prefer shared form/dialog/list/tab/display primitives and export controllers to new parallel implementations.
- Backend background work belongs to the runtime task owner. Register periodic jobs through the existing
  job definitions and preserve ordered startup and bounded shutdown.
- Use the outbound HTTP policy and source dispatch pacing for matching callers. Retryable reads, paid LLM
  submissions, Slack and durable webhook deliveries have different policies; do not add generic nested retries.
- Keep cloud calls inside adapters and consume normalized results and capabilities. Preserve Organization
  scope before writes or provider effects.
- Use module loggers from `app.logging_config`, fixed diagnostic text and safe structured fields. Never log
  raw provider responses or credentials. Follow [Logging](deployment-guide.md#logging) for delivery and limits.

Dependency review checks newly introduced high/critical risk on pull requests. The scheduled frontend audit
also checks the committed lock, including development dependencies. Approved, dated exceptions live only in
`.github/security-exceptions.json`; expired exceptions are inactive. Update schedules and package groups
are committed in [.github/dependabot.yml](../.github/dependabot.yml). GitHub's repository-level switches for
security-update PRs, Actions notifications and code scanning are separate service settings.
