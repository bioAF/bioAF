### Release process

- Switched from SemVer to CalVer (`YYYY.M.D.N`). The release workflow now
  computes the next version on each push to `main`, assembles a changelog
  section from per-PR snippets under `changes/unreleased/`, commits the
  version bump back to `main`, tags the release, and publishes Docker
  images. No more manual version edits.
- The Update button continues to work across the cutover. SemVer clients on
  any `0.x` or `1.x` release will see the first CalVer release as newer and
  can install it directly.
