### CI and releases

- Pull requests now run only the checks their changes need: documentation-only changes skip the application test
  suites, CI-configuration-only changes add workflow linting, and everything else (including any workflow change)
  runs the full suite. The `ci:full` label forces full validation. See CONTRIBUTING.md.
- The backend benchmark accepts only `auto` or 1 to 4 workers and rejects anything else before installing or running
  tests.
- Workflow actions are pinned to reviewed commits, and the secret scanner runs a pinned image instead of `latest`.
- Checkouts no longer keep their credential, and the release token is used only to push the signed release commit
  and tag.
- The release commit and tag are pushed together: if a concurrent merge rejects the commit, no tag is created.
