### Infrastructure

- Failed infrastructure deploys now raise a "Deployment failed" notification.
  Previously the notification had no emitter and never fired on a real Terraform
  apply failure.
- Consolidated Terraform execution onto a single owner (`TerraformExecutor`).
  Removed the legacy per-component Terraform "configure" flow, which produced
  no-op plans, and its standalone component detail page. Enabling and disabling
  components on the Infrastructure > Components screen is unchanged.

### Fixes

- Notebook and RStudio container image builds are reliable again. The image was
  floating on `:latest` everywhere, which left its R too old for current
  packages and broke the build. Pinned the base image, R (4.4.3), Bioconductor
  (3.20), the CRAN snapshot, and the Python packages, and added the OpenBLAS
  runtime so the single-cell/Seurat stack installs cleanly.
