### SDKs

- Python SDK: `snapshot()` no longer drops a whole `adata.uns` parameter set
  when one of its values is a multi-element NumPy array. That value is now
  recorded as text. Scalars and single-element arrays keep their values.
- R SDK: the package now installs as a built source package and passes `R CMD
  check` without errors (one warning remains, about `Seurat::Layers`). It
  ships the MIT license file its DESCRIPTION refers to, declares `curl`,
  `methods` and `stats` (used by existing code) and the test packages `withr`
  and `mockery`, states its R 4.1.0 minimum, and documents
  `extract_seurat_metadata`.

### Continuous integration

- Pull requests now run the Python SDK tests against its built wheel, the R SDK
  tests (with Seurat) and `R CMD check` against its built package, the Bats
  suites for `./bioaf`, the installers and the update agent, and Terraform
  validation for both the GCP and AWS modules on Terraform 1.10.5, the version
  the backend image ships.
- The required Coverage Report check now fails unless every CI job succeeded.
- CI steps now run with `pipefail`, so a failing test run piped into a log can
  no longer pass its job.
