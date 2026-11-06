### Internal

- The backend test suite runs about five times faster: roughly 3 minutes at `-n 4` instead of 16, with the same tests and outcomes. No application behavior changes.
- Tests hash passwords, API keys, and setup codes at a lower bcrypt cost through a test-only switch (`BIOAF_TEST_BCRYPT_ROUNDS`, default 4). Production keeps cost 12, and tests assert it.
- Most database tests now share a schema built by running the migrations once per worker, then reset or rolled back after each test, instead of creating and dropping every table per test. Tests therefore run against the production constraints. Files that depend on state production never has keep the old per-test schema.
- Each pytest invocation uses its own recorded schemas, so concurrent and serial runs no longer collide or wipe `public`. `python -m tests.plugins.invocation cleanup <id>` removes the schemas of an abandoned run.
- Fixed test isolation gaps: background work through module-level session factory aliases could reach the non-test database; module caches, event bus subscribers, settings, and encryption keys leaked between tests; background tasks from session-only tests were never cancelled.
- New test tooling: per-phase timing reports (`--phase-report`), a seeded, replayable shuffle of test order (`--shuffle-seed`), and disposable PostgreSQL servers for benchmarks. CI keeps timing and JUnit artifacts, and a manual workflow runs backend benchmarks.
- Tests now pin known differences between the migrated schema and the models (9 uniqueness rules exist only in migrations, and 28 columns are NOT NULL only in the models) until they are reconciled.
