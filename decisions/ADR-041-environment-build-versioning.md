# ADR-041: Environment Build Versioning

**Status:** Accepted
**Date:** 2026-04-04
**Deciders:** Brent (repository owner)

---

## Context

ADR-033 introduced versioned compute environments where users create named environments, define them via Dockerfile or conda spec, and build versioned container images. Version numbers are integers (v1, v2, v3), and the ADR states: "once a version reaches `ready`, its image is never overwritten."

In practice, scientists sometimes need to rebuild an existing version. The Dockerfile may reference `FROM rocker/rstudio:latest` or install packages without pinning versions. When the base image or package repository is updated upstream, a rebuild of the same Dockerfile produces a different image. Common scenarios:

- A security patch to the base image requires rebuilding all active environments
- A scientist wants to pick up a bugfix in the bioAF base image without changing their custom packages
- An environment build failed due to a transient network error and needs to be retried after the version was already marked `ready` from a partial cache hit

The current system has no way to distinguish between "v1 built on March 1" and "v1 rebuilt on April 4 with newer base packages." If the image is overwritten in Artifact Registry, provenance records pointing to `env:v1` silently refer to a different image than the one that produced the original results.

---

## Decision

### 1. Add `build_number` to `EnvironmentVersion`

Each `EnvironmentVersion` row gains a `build_number` column (integer, default 1). The combination of `(environment_id, version_number, build_number)` is unique.

```text
environment_versions
  ... existing columns ...
  build_number  INTEGER  NOT NULL  DEFAULT 1

  UNIQUE (environment_id, version_number, build_number)
```

### 2. Rebuild creates a new row

Rebuilding v1 does not overwrite the existing row. Instead, it creates a new `EnvironmentVersion` with the same `environment_id` and `version_number` but an incremented `build_number`:

| version_number | build_number | status | image_uri |
| --- | --- | --- | --- |
| 1 | 1 | ready | env:v1 |
| 1 | 2 | building | (pending) |

The original v1 build 1 record and its image remain untouched. The rebuild (build 2) goes through the standard draft/building/ready lifecycle.

### 3. Image tags include build number

Images are tagged as `{env_name}:v{version}.{build}` in Artifact Registry:

```text
us-central1-docker.pkg.dev/project/bioaf-images/seurat-gpu:v1.1    (original)
us-central1-docker.pkg.dev/project/bioaf-images/seurat-gpu:v1.2    (rebuild)
us-central1-docker.pkg.dev/project/bioaf-images/seurat-gpu:v2.1    (new version)
```

The first build of any version uses build number 1 (tagged `v1.1`). This is a change from the current `v1` tag format but is backward-compatible: existing images in the registry are not renamed, and old `EnvironmentVersion` rows default to `build_number=1`. New builds use the dotted format going forward.

### 4. Minor versions do not collide with major versions

Scientists create major versions (v1, v2, v3) by writing new Dockerfiles or modifying the environment definition. Rebuilds create minor versions (v1.1, v1.2) within the same major. The namespace is separate: rebuilding v1 produces v1.2, never v2. Scientists' intentional version bumps are never trampled by infrastructure rebuilds.

### 5. Provenance links to exact builds

Session records store `environment_version_id` as a foreign key. Since each build is a separate row, the FK naturally points to the exact build used. The provenance system (ADR-037) can report both the version number and build number, answering: "This analysis used seurat-gpu v1, build 2 (rebuilt 2026-04-04)."

---

## Alternatives Considered

**Overwrite the image tag on rebuild:** This is the current behavior. The v1 tag in Artifact Registry points to whatever was built last. Provenance records become unreliable because `env:v1` could refer to different images at different points in time. Rejected because it breaks reproducibility.

**Use content-addressable tags (SHA digest):** Every Docker image has a SHA256 digest. We could store the digest and pull by digest instead of tag. This is maximally precise but unfriendly to humans ("which image is sha256:a3f8c2...?") and makes the UI harder to navigate. Rejected as the primary scheme, though digests could be stored alongside tags as an additional integrity check in the future.

**Treat every rebuild as a new major version:** Rebuilding v1 would create v2. Simple, but misleading: v2 implies a deliberate change to the environment definition, not an infrastructure-level rebuild of the same spec. Scientists would lose the semantic distinction between "I changed my packages" and "the base image was patched." Rejected.

---

## Consequences

**Positive:**

- Every build is immutable and traceable. Provenance records always point to the exact image that was used.
- Scientists can rebuild environments for security patches or base image updates without losing the original build record.
- The minor version scheme (v1.1, v1.2) communicates clearly: "same definition, different build." Major versions (v1, v2) communicate: "different definition."
- Backward-compatible: existing rows get `build_number=1` via server default, no data migration needed.

**Negative:**

- Artifact Registry accumulates more images (one per build instead of one per version). Storage costs increase proportionally. A future cleanup policy for old builds may be needed.
- The UI must now display build numbers alongside version numbers. The display format `v{version}.{build}` is simple but adds visual complexity to version selectors.
- Old image tags (`env:v1` without build number) coexist with new tags (`env:v1.1`). The build service must handle both formats during the transition period.

**Neutral:**

- The `UNIQUE(environment_id, version_number, build_number)` constraint prevents accidental duplicate builds at the database level.
- The rebuild API endpoint (`POST /environments/{id}/versions/{vid}/rebuild`) follows the existing pattern of version lifecycle endpoints.

---

## References

- ADR-033 (Versioned compute environments -- base version system this extends)
- ADR-031 (Notebook image build pipeline -- Cloud Build integration)
- ADR-037 (Provenance reporting -- environment details in provenance records)
- ADR-040 (Notebook file lifecycle -- environment version linking for sessions)
