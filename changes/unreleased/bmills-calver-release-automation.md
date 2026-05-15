### Release process

- Switched from SemVer to CalVer (`YYYY.MM.N`, e.g. `2026.5.0` for the first
  release in May 2026, `2026.5.1` for the second). The release workflow now
  computes the next version on each push to `main`, assembles a changelog
  section from per-PR snippets under `changes/unreleased/`, commits the
  version bump back to `main`, tags the release, and publishes Docker
  images. No more manual version edits.
- The Update button continues to work across the cutover with no user
  action required. CalVer tags are three numeric segments, so the deployed
  install validator on every existing client (including pre-cutover `0.x`
  releases) accepts them; tuple comparison correctly recognizes them as
  newer than any prior SemVer.
