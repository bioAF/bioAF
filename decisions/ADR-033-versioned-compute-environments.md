# ADR-033: Versioned Compute Environments

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** Brent (repository owner)

---

## Context

bioAF currently builds a single notebook container image (`bioaf-scrna`) via Cloud Build (ADR-031). The Dockerfile is embedded as a string constant in `NotebookImageService` and tagged as `latest`. All notebook sessions use the same image. This has three limitations:

1. **No version pinning.** A scanpy 1.9 analysis may not reproduce on scanpy 1.10. When the embedded Dockerfile is updated and rebuilt, the old environment is gone. There is no way to pin a session to the exact package versions that produced a specific result.
2. **No customization.** Teams that need packages not in the default image (e.g., a custom R library, a specific version of Seurat, or a GPU-accelerated toolkit like rapids-singlecell) cannot add them without modifying the embedded Dockerfile in the source code.
3. **Single image.** Different team members working on different projects may need different environments simultaneously. One researcher needs Seurat v5 for a new project while another needs Seurat v4 for a legacy analysis heading to publication.

The upcoming custom work nodes (ADR-034) make this more urgent. Work nodes are team-configured compute sessions where the environment definition is the primary user-facing configuration.

---

## Decision

Introduce a versioned environment definition system where users create named environments, upload Dockerfile or conda environment specs, build versioned images via Cloud Build, and select a specific environment version when launching notebook sessions or work nodes.

### Environment Definition Formats

The platform accepts two definition formats:

- **Dockerfile:** Full control over the image. Users upload or paste a Dockerfile that is submitted directly to Cloud Build. Supports arbitrary system packages, multi-stage builds, and custom base images.
- **Conda environment file (environment.yml):** Familiar to computational biologists who use conda. The platform wraps the conda file in a generated Dockerfile that installs conda, creates the environment, and activates it as the default. Users who start with conda can later "eject" to a raw Dockerfile for more control.

Both formats are stored as text in the database and can be parsed by the UI for visual editing (add a package, change a version, save as a new version).

### Data Model

```text
environments
  id, name, description, organization_id, created_by (FK users),
  visibility (enum: team, organization),
  created_at, updated_at

environment_versions
  id, environment_id (FK), version_number (auto-increment per environment),
  status (enum: draft, building, ready, failed),
  definition_format (enum: dockerfile, conda),
  definition_content (text -- the Dockerfile or environment.yml),
  build_id (Cloud Build ID, nullable),
  image_uri (Artifact Registry URI, nullable -- set on successful build),
  created_by (FK users), created_at
```

### Version Lifecycle

1. **Draft.** User creates a new version by uploading a Dockerfile or conda file (or by cloning and editing an existing version). The version is saved but not built.
2. **Building.** User triggers a build. The platform submits the definition to Cloud Build (reusing the ADR-031 pipeline). Status is polled by the existing background loop.
3. **Ready.** Build succeeded. The `image_uri` is written. This version can now be selected when launching sessions or work nodes.
4. **Failed.** Build failed. The user can view build logs, edit the definition, and retry.

Multiple versions of the same environment can be in `ready` state simultaneously. Users select a specific version at launch time. There is no "active" version -- the choice is explicit.

### Image Tagging

Images are tagged as `{env_name}:{version_number}` in Artifact Registry:

```text
us-central1-docker.pkg.dev/my-project/bioaf-images/seurat-gpu:1
us-central1-docker.pkg.dev/my-project/bioaf-images/seurat-gpu:2
us-central1-docker.pkg.dev/my-project/bioaf-images/scrna-default:1
```

Version-numbered tags are immutable. Rebuilding a failed version overwrites its tag, but once a version reaches `ready`, its image is never overwritten.

### Visibility and Permissions

- **Team visibility:** Only users within the creator's team (or admins) can see and launch from this environment. This is the default.
- **Organization visibility:** All users in the organization can see and launch from this environment. Admins and users with the `environments.create` permission (ADR-032) can set this.

The `environments.create` permission controls who can create environments and trigger builds. By default, this is granted to the `admin` and `comp_bio` built-in roles. Admins can grant it to custom roles as needed.

### UI Editing

The UI provides a code editor for the raw Dockerfile or conda file, plus a structured editing layer that can:

- Parse an existing Dockerfile or conda file to extract package names and versions
- Allow adding, removing, or updating packages via form controls
- Save changes as a new version of the environment (the original version is never modified)

The structured editor is a convenience layer. Users can always edit the raw text directly.

### Default Environment

The current `bioaf-scrna` image becomes a system-managed environment called "Default scRNA-seq" with `visibility: organization`. It is seeded during migration with the existing embedded Dockerfile as version 1. The embedded Dockerfile constant in `NotebookImageService` is removed; all builds go through the environment system.

### Cloud Build Integration

The existing `NotebookImageService.build_notebook_image()` is generalized to `EnvironmentBuildService.build_version(environment_version_id)`. It reads the definition content from the environment version record instead of the embedded string. Build configuration (machine type, timeout, logging) remains the same as ADR-031. The background polling loop polls all in-progress builds, not just one.

---

## Consequences

**Positive:**

- Teams can define and maintain environments tailored to their specific analysis needs
- Multiple versions coexist in Artifact Registry with immutable tags, enabling reproducibility
- Conda support meets comp bios where they are; Dockerfile support provides an escape hatch for complex setups
- The structured UI editor lowers the barrier for users who don't know Docker
- Existing deployments get the "Default scRNA-seq" environment automatically; no breaking change

**Negative:**

- Each built image consumes Artifact Registry storage (several GB per version). Organizations with many versions will accumulate storage costs. A future cleanup policy for old, unused versions may be needed.
- Build times (30-60 minutes for R/Bioconductor) mean users cannot iterate quickly on environment definitions. Encouraging conda-based definitions (which use pre-compiled binaries) mitigates this.
- Two definition formats (Dockerfile + conda) doubles the parsing and validation surface in the UI editor.

**Neutral:**

- The "Default scRNA-seq" environment replaces the embedded Dockerfile, consolidating all image management into one system
- Environment versions are append-only (new versions, never edits to existing ones), which simplifies the data model and audit trail

---

## References

- ADR-031 (Cloud Build pipeline -- build infrastructure reused by this system)
- ADR-032 (custom RBAC -- `environments.*` permissions)
- ADR-034 (custom work nodes -- environment selection at launch time)
- ADR-021 (Kubernetes compute backend -- images pulled by K8s pods)
