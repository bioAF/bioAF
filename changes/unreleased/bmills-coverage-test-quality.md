### Internal

- Pull requests now fail CI when combined-suite coverage drops below its floor. Floors are set per stack and per metric, in `scripts/ci/coverage_policy.json`: backend lines 83% and branches 69%, frontend lines 75% and branches 77%. A failed, interrupted, or missing test run fails the gate instead of passing on partial numbers. Floors are raised by hand; the coverage comment notes when a metric is a point or more above its floor.
- Backend coverage now measures branches as well as lines. The coverage comment shows line and branch columns for each stack and area, and a Gates table.
- The backend test run in CI no longer stops at the first failure, so a failing run reports every failure.
- A reviewed unit selection (`python -m tests.support.unit_selection`, run from `backend/`) runs without PostgreSQL. CI measures it in its own step and reports it in a separate table; it is not gated.
- Tests that only read service source code for adapter names were replaced with tests that run the pipeline and notebook services against recording adapters. Notebook output registration on stop and the idle-session auto-stop now have executed tests; neither had one before. Removed 18 test patches of an SSH method nothing calls. No application behavior changes.
