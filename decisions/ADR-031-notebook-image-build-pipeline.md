# ADR-031: Cloud Build Pipeline for Notebook Container Images

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** Brent (repository owner)

---

## Context

bioAF's notebook sessions (RStudio, JupyterHub) run as Kubernetes Pods that need a container image pre-loaded with the scRNA-seq analysis toolchain: scanpy, anndata, scvi-tools, Seurat, Bioconductor packages, and RStudio Server. This image (`bioaf-scrna`) is large (several GB) and takes 30-60 minutes to build due to R/Bioconductor package compilation.

ADR-021 established that notebook Pods run on the `bioaf-interactive` node pool and mentioned that the `bioaf-scrna` image is published to Artifact Registry, but did not specify how the image is built or who triggers the build. Three options were considered:

1. **Pre-built public image.** Publish `bioaf-scrna` to a public registry (Docker Hub, GitHub Container Registry). Users pull it directly. Problem: the image must be customized per deployment (GCS credentials, org-specific packages), and public images cannot include customer-specific tooling.
2. **Local Docker build.** Users run `docker build` on their workstation or a CI runner. Problem: the build takes 30-60 minutes, requires Docker installed locally, and the resulting image must be pushed to the customer's Artifact Registry manually.
3. **Cloud Build triggered from the UI.** The bioAF backend submits a Cloud Build job that builds the image in GCP and pushes it to the customer's Artifact Registry. The UI tracks build progress and blocks notebook launches until the image is ready.

---

## Decision

Build the `bioaf-scrna` notebook image via Google Cloud Build, triggered automatically when an admin enables the RStudio or JupyterHub component in the UI. The build runs entirely in GCP using the customer's project resources. No local Docker installation is required.

### Architecture

```text
Admin enables RStudio/JupyterHub
         |
         v
  Component Toggle API
         |
         v
  NotebookImageService.build_notebook_image()
         |
         +-- ensure Artifact Registry repo exists
         +-- upload Dockerfile to GCS working bucket
         +-- submit Cloud Build job via REST API
         +-- set component status = "provisioning"
         |
         v
  Background polling loop (30s interval)
         |
         +-- poll Cloud Build status
         +-- on SUCCESS: write image URI to platform_config
         +-- on FAILURE/TIMEOUT: set component status = "build_failed"
         |
         v
  Notebook session launch reads image URI from platform_config
```

### Build Configuration

| Setting | Value | Rationale |
| --- | --- | --- |
| Machine type | `E2_HIGHCPU_8` | R/Bioconductor compilation is CPU-bound |
| Timeout | 7200s (2 hours) | First build compiles R packages from source |
| Logging | `CLOUD_LOGGING_ONLY` | Avoids requiring a separate logging bucket |
| Registry | Artifact Registry (`bioaf-images` repo) | Regional, IAM-integrated, GCR successor |

### Dockerfile

The Dockerfile is embedded as a string constant in `NotebookImageService`, not read from disk. This allows builds to run without requiring the bioAF source repository to be present in the Cloud Build environment. The image includes:

- **Base:** `jupyter/scipy-notebook:latest`
- **System:** HDF5, R, build tools
- **Python:** scanpy, anndata, scvi-tools, leidenalg, pandas, numpy, matplotlib, seaborn, plotly, umap-learn, bbknn, scrublet, google-cloud-storage
- **R:** Seurat, ggplot2, tidyverse, pheatmap, devtools, BiocManager, SingleCellExperiment, scater, scran
- **Tools:** RStudio Server 2024.04.2, gsutil (GCS home directory sync)

A reference `Dockerfile.bioaf-scrna` exists in `docker/` for manual builds and documentation, but the authoritative build source is the embedded string in the service.

### Artifact Registry

The service creates the `bioaf-images` repository automatically via the Artifact Registry REST API if it does not exist. The image is tagged as `latest` at `{region}-docker.pkg.dev/{project_id}/bioaf-images/bioaf-scrna:latest`.

### State Tracking

Build state is stored in `platform_config` (key-value table):

| Key | Purpose |
| --- | --- |
| `bioaf_scrna_image` | Full image URI (set only after successful build) |
| `notebook_image_build_id` | Current/last Cloud Build ID |
| `notebook_image_build_status` | QUEUED, WORKING, SUCCESS, FAILURE, CANCELLED, TIMEOUT |

### Component State Transitions

When a notebook component (RStudio or JupyterHub) is toggled on:

1. If no image URI exists or the last build was not successful, trigger a new build
2. Set component status to `provisioning`
3. On build success: set to `enabled`, write image URI
4. On build failure: set to `build_failed`

Users can retry by toggling the component off and on again. An in-flight build can be cancelled via the API.

### Session Launch Guard

The notebook session launch endpoint checks `bioaf_scrna_image` before creating a Pod. If the image URI is not set or a build is in progress (QUEUED/WORKING), the launch is rejected with an error message directing the user to wait for the build to complete.

### REST API Over SDK

The service uses `urllib.request` with `google.auth` for Cloud Build and Artifact Registry REST calls instead of the `google-cloud-build` Python SDK. This avoids adding heavy gRPC dependencies to the backend's requirements.

---

## Consequences

**Positive:**

- Zero local tooling required -- the entire build runs in GCP
- Admin enables a component and the image builds automatically; no manual Docker commands
- Build progress is visible in the UI with status polling
- Artifact Registry is regional and IAM-integrated, so no separate auth configuration
- Embedded Dockerfile means builds work without access to the source repository

**Negative:**

- First build takes 30-60 minutes due to R/Bioconductor compilation from source; users must wait before launching their first notebook session
- Cloud Build costs (~$0.003/build-minute on e2-highcpu-8) are borne by the customer's GCP project
- The embedded Dockerfile can drift from the reference `docker/Dockerfile.bioaf-scrna`; keeping them in sync is a manual process
- `latest` tag means there is no image versioning; a failed rebuild could leave the platform without a working image until the build is retried

**Neutral:**

- The background polling loop (30s interval) is lightweight and runs within the existing FastAPI lifespan
- Build cancellation is supported but not surfaced prominently in the UI (admin-only API endpoint)

---

## References

- ADR-021 (Kubernetes compute backend -- `bioaf-interactive` node pool, `bioaf-scrna` image)
- ADR-030 (Session credentials -- PAM auth requires RStudio Server installed in the image)
- ADR-001 (GCP-only -- Cloud Build and Artifact Registry are GCP services)
- #154 (Notebook K8s launch, UI overhaul, and infrastructure improvements PR)
