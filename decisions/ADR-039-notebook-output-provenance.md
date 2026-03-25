# ADR-039: Notebook Output Provenance

**Status:** Accepted
**Date:** 2026-03-25
**Deciders:** Brent (repository owner)

---

## Context

Interactive compute sessions (Jupyter, RStudio, VSCode) are a first-class feature of the platform. Scientists use them to perform exploratory analysis, generate figures, produce derived datasets, and create analysis snapshots. These sessions can produce outputs that become significant scientific artifacts -- a figure in a paper, a filtered cell matrix passed to the next pipeline stage, a checkpoint that becomes the basis for further analysis.

The platform currently has no way to trace these artifacts. Two specific gaps exist:

**Gap 1: `notebook_session_files` is not a first-class model.** The table was created by a migration (`notebook_session_files`) but was never promoted to an ORM model. It has no relationship on `ComputeSession`. The `delete_file_record` function references it through a raw SQL existence check at runtime -- a sign it was added as an afterthought. There is no relationship to query, no schema validation, and no service-layer abstraction around it.

**Gap 2: Files produced by notebooks are semantically invisible.** `File.source_type` has two values: `upload` and `pipeline_output`. A figure produced by a Jupyter notebook, a CSV exported from an RStudio session, a filtered anndata object written to GCS from a notebook -- all of these land in the system as `source_type="upload"` or are not tracked at all. The provenance system (ADR-037) cannot distinguish a notebook output from a user upload, and `ArtifactProvenanceData.downstream_usage` misses all notebook consumption entirely.

Together, these gaps mean the platform cannot answer: "Which notebook session produced this figure?" or "Which files did this notebook session access?"

---

## Decision

### 1. Promote `notebook_session_files` to an ORM model

Create `NotebookSessionFile` as a proper SQLAlchemy model:

```text
notebook_session_files
  id            INTEGER     PK
  session_id    INTEGER     FK → compute_sessions.id  NOT NULL
  file_id       INTEGER     FK → files.id             NOT NULL
  access_type   VARCHAR(20) NOT NULL  DEFAULT 'input'   -- 'input' | 'output'
  accessed_at   TIMESTAMPTZ DEFAULT now()

  UNIQUE (session_id, file_id, access_type)
```

`ComputeSession` gains an `accessed_files` relationship via this model.

`File` gains a `notebook_sessions` back-reference via the same model.

`delete_file_record` removes its runtime table existence check and uses the ORM relationship directly.

### 2. Add `notebook_output` as a valid `File.source_type`

Valid values for `File.source_type` become: `upload`, `pipeline_output`, `notebook_output`.

### 3. Add `source_notebook_session_id` FK to `File`

`File` gains a nullable `source_notebook_session_id` FK to `compute_sessions.id`, mirroring the existing `source_pipeline_run_id`. This field is set when a file is created via notebook session upload or output registration.

### 4. Update the provenance data gatherer

`ProvenanceDataGatherer.gather_artifact()` queries `notebook_session_files WHERE file_id = :fid AND access_type = 'input'` to populate notebook consumption in `downstream_usage`.

`gather_artifact()` also resolves `source_notebook_session_id` if set, adding the session context to the artifact's source lineage.

### Session-side tracking

When files are written or registered from a compute session, the session service writes a `NotebookSessionFile` row with `access_type='output'`. When files are explicitly mounted or opened (to the extent the system can detect this), rows with `access_type='input'` are written. Full input tracking depends on the notebook environment reporting back via the session API -- this is best-effort for existing sessions and can be made comprehensive when combined with ADR-034 work node improvements.

---

## Alternatives Considered

**Keep `notebook_session_files` as a raw migration table:** Continues to work for the narrow case of tracking session-file linkage, but prevents any service-layer abstraction, relationship loading, or provenance integration. Provenance completeness (ADR-037) is not achievable. Rejected.

**Track notebook outputs only in `AnalysisSnapshot`:** Snapshots cover structured analysis checkpoints (Seurat objects, anndata objects). They do not cover general file outputs -- exported CSVs, figures, intermediate matrices. A figure saved as a PNG is not a snapshot. Rejected as insufficient scope.

**Derive provenance from GCS access logs:** Technically possible (GCS audit logs record reads and writes by service account) but requires BigQuery integration, is not real-time, and adds significant operational complexity. Rejected in favor of application-level tracking.

---

## Consequences

**Positive:**

- Notebook-produced artifacts are now traceable through the provenance system (ADR-037).
- `delete_file_record` no longer contains a fragile runtime existence check against `information_schema`.
- The `ComputeSession` model has a proper relationship to its associated files, queryable via ORM.
- Scientists can see which session produced a figure or output file in the file detail modal.

**Negative:**

- Input file tracking in notebooks is best-effort. Files a scientist opens interactively without using the session file registration API will not appear as inputs. This is a fundamental limitation of interactive sessions -- the system cannot intercept all file reads. Notebook outputs are reliably tracked; notebook inputs are tracked only when the session explicitly registers them.
- Adding `source_notebook_session_id` to `File` continues the pattern of session-scoped source FKs. If a third compute type is added in the future, a third FK column would be added. This is acceptable given the small set of compute types in scope.

**Neutral:**

- The existing `notebook_session_files` table data is compatible with the new model -- no data migration is required, only the addition of the ORM class and relationship.
- `AnalysisSnapshot.notebook_session_id` already links snapshots to sessions. This ADR does not change that relationship.

---

## References

- ADR-037 (Provenance reporting -- artifact downstream usage requires notebook session data)
- ADR-030 (Session credentials and PAM auth -- compute session architecture)
- ADR-031 (Notebook image build pipeline -- session types and output conventions)
- ADR-034 (Custom work nodes -- session API improvements that enable more complete input tracking)
- ADR-009 (Immutable audit log -- provenance chain must be complete for all artifact types)
