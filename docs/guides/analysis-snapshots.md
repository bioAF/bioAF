# Analysis Snapshots

The Python and R SDKs record selected analysis-object metadata, notes and optional artifacts in bioAF.
They use the internal API and its permissions, not the public Integration API. A snapshot is not a complete
backup of a notebook, process or dataset.

## Python SDK

Install from a checkout of this repository; this does not depend on a package-index publication:

```bash
python -m pip install './sdk/python[anndata,matplotlib,checkpoint]'
```

The package name is `bioaf-sdk` and the import is `bioaf`. From an environment with `BIOAF_API_URL` and
`BIOAF_TOKEN` set, use the actual function interface:

```python
import bioaf

bioaf.connect()  # reads API URL and token from the environment
result = bioaf.snapshot(
    adata,
    label="Clustering at resolution 0.5",
    notes="Reviewed cluster labels before downstream comparison.",
    experiment_id=42,
    figure=fig,  # optional matplotlib Figure, path or bytes
    save_checkpoint=False,
)
print(result["id"])
```

`adata` and `fig` are objects from your analysis. `bioaf.connect(api_url=..., token=...)` can supply explicit
connection values. There is no `SnapshotClient` class or `tags`/`cluster_key` argument in the current SDK.
Record analysis choices in the object and `notes`; extraction reads the supported AnnData metadata.

`BIOAF_EXPERIMENT_ID`, `BIOAF_PROJECT_ID` and `BIOAF_SESSION_ID` set default associations when present at
connection time. Explicit `experiment_id`/`project_id` override those defaults. Use integer record IDs,
not human-readable experiment codes. The managed-session launchers do not inject these variables, and
the bundled image definitions do not install the SDK. Install it in the environment you actually use and
set the connection/association values explicitly.

## R SDK

Install the package's dependencies (`curl`, `httr2` and `jsonlite`), then install from a checkout:

```r
install.packages("sdk/r", repos = NULL, type = "source")
library(bioaf)
bioaf_connect()  # reads the same BIOAF_* connection and association variables
result <- bioaf_snapshot(
  seurat_object,
  label = "Reviewed clustering",
  notes = "Marker annotations checked.",
  experiment_id = 42L,
  save_checkpoint = FALSE
)
```

The R interface also accepts `project_id` and an optional figure file path. Seurat extraction and
checkpoint support require their optional dependencies. See [sdk/r/DESCRIPTION](../../sdk/r/DESCRIPTION).

## Artifacts and Error Handling

Metadata extraction is best-effort. Figure/checkpoint upload can warn and still allow the metadata
snapshot to be created. Inspect the returned file associations if those artifacts matter to your record.
Checkpoint capture stores selected metadata/embeddings; it is not a full AnnData/Seurat object backup.
A failed final API request raises instead of confirming a snapshot.

Create/list/detail operations enforce Organization scope and their experiment/project permissions. Foreign
record or artifact references are refused. Project snapshot timelines/counts also include snapshots whose
experiment belongs to that project; this listing rule is not a blanket expansion of project provenance.

## List, Compare and Star

Use the experiment/project's snapshot or analysis history controls where available. Internal API examples:

```text
GET /api/snapshots?experiment_id=42
GET /api/snapshots?project_id=7
GET /api/snapshots/compare?ids=101,102
POST /api/snapshots/101/star
```

Comparison accepts 2-5 snapshot IDs and returns parameter, embedding, clustering and command-log differences
where captured. Starring requires `experiments:change_status`; read access alone does not permit it.
The Python/R SDKs do not expose dedicated compare/star methods; use the UI or authenticated internal API.
