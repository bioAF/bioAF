### Security

- Restrict GitHub Actions workflows to read-only `contents` permission so the
  default `GITHUB_TOKEN` cannot mutate the repository, addressing 10 code
  scanning alerts in `ci.yml`, `build.yml`, and `changelog-check.yml`.
