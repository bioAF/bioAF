# Computational Biologist User Guide

This guide covers pipelines, interactive compute, outputs and research provenance. Built-in `comp_bio`
permissions are a starting point; custom roles, component readiness and provider capabilities govern the
actual controls you see.

## Launch and Monitor Pipelines

1. Open **Pipelines > Pipeline Catalog**, or **Custom Pipelines** for a user-authored workflow.
2. Select an installed/enabled pipeline and version. Review its required inputs and parameter schema.
3. Choose the experiment, Samples and files appropriate to the run; review resources and parameters.
4. Submit, then follow the record under **Pipelines > Pipeline Runs**.

Catalog contents depend on what your Organization installed. Sample-sheet construction validates linked
inputs; missing or ambiguous files must be corrected before launch. The launch preflight can report
problems before a run is created.

If submission raises after the run exists, bioAF records a failed run with `submission_failed`. This means
it could not confirm cluster acceptance, not proof that no cloud job exists. Ask an administrator to inspect
the run/job before relaunching to avoid duplicate work. See [Compute Stack Setup](guides/compute-stack-setup.md).

Review run logs, registered outputs and QC, then record a scientist's review/handoff decision as appropriate.
This review is advisory: it does not gate data access or later pipeline launches. An affirmative review can
move an experiment from `pipeline_complete` to `reviewed`. Agent Review is a separate AI advisory note.

## Templates and Environment Versions

**Pipelines > Pipeline Templates** and **Workbench > Workbench Templates** manage the relevant Environment
families. Create a template/environment, add a version from a Dockerfile or conda definition, then build it.
A launch selects a successful `ready` version. Container builds use Cloud Build/Artifact Registry on the GCP
reference path and have CodeBuild/ECR implementations on AWS; full AWS parity remains incomplete.

Versioned definitions keep earlier images available for reproducibility. See
[Custom Pipelines](guides/custom-pipelines.md) for its version-cascade and runtime contracts.

## Notebook Sessions

Open **Workbench > Notebook Sessions** and launch Jupyter or RStudio with a ready template version,
resource profile and any applicable Project/Experiment scope.

- A project-only launch records the project.
- An experiment-only launch records that experiment and its project, when it has one.
- If both are selected, the experiment must belong to that project. Standalone experiments remain supported.
- Foreign Organization IDs and environment versions are refused before a workload is created.

The session list and **Recreate** retain recorded scope. Recreate pre-fills a new launch; review its version,
resources and inputs before confirming. Project scope does not by itself mount extra files.

Use the session's storage-sync and output-registration actions to preserve work and its provenance. Session
credentials support RStudio login. Idle-timeout behavior follows Workbench settings and session status;
check the page rather than assuming a browser tab keeps a session alive.

## SSH Work Nodes

Open **Workbench > Work Nodes** for a standalone GCE VM (with an EC2 implementation on AWS). Choose a ready
Work Node environment version, resources and scope. Work Nodes are separate from notebook/pipeline pods.
Use the displayed connection details and preserve outputs before stopping the node.
See [SSH and Exec Access](guides/ssh-access.md) for access prerequisites and the audit boundary.

## Files, Projects and Provenance

Use **Data & Files > Files** to browse and associate registered files. Large experiment selectors search by
name and support paging. Use [Reference Data](guides/reference-data.md) to manage reference inputs, and
[Cross-Experiment Projects](guides/cross-experiment-projects.md) to understand membership and scope.

Record significant analysis states with the [Python/R SDK](guides/analysis-snapshots.md). The snapshot records
selected object metadata, notes and optional artifacts; it is not a complete capture of every notebook action.
Inspect provenance from the experiment/project and registered outputs.

Use [Data Exports](guides/data-exports.md) for file archives and [GEO Export](guides/geo-export.md) for submission
metadata. These exports have different contents and delivery contracts.

## Literature and AI

Use **Lab Knowledge > Literature Library** for papers and AI Literature Review. The floating assistant can
help inspect records and propose actions. Consequential actions require approval, and provider failures
should be retried explicitly after review. [Paper Validation](guides/paper-validation.md) is a beta workflow
with assay/evidence limitations. See [Literature and AI Workflows](guides/literature-and-ai.md).
