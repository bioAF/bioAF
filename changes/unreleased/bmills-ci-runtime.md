### CI

- Routine Dependabot updates arrive as grouped pull requests on separate weekday mornings (Pacific): backend on
  Tuesday, frontend on Wednesday, workflow actions on Thursday. Minor and patch updates for the backend and for
  workflow actions are grouped; major upgrades and security updates still arrive as their own pull requests.
- The full-scope backend benchmark now runs exactly the CI backend test command on the CI runner image, with full
  Git history and CI's coverage report, so its results apply to CI.
- Every backend test file is now listed in the test isolation inventory, and a check fails when a new file is not.
