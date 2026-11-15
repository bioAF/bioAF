# Cross-Experiment Projects

Projects organize research spanning experiments and selected Samples. An Experiment remains the primary
study record; it can belong to a Project, and a Project can also include Samples through explicit membership.

## Create and Populate a Project

Open **Experiments > Project List** and choose **New Project**. Enter a name, description and hypothesis as
appropriate. Assign a new experiment to the Project during experiment creation, or use the project's
sample-association controls to include selected existing Samples.

Project detail combines Samples from experiments whose `project_id` matches the Project with explicitly
linked Samples, deduplicating membership. Explicit membership does not move a Sample out of its original
experiment. The server checks all supplied Sample IDs before changing the project; foreign Organization
IDs fail the request rather than partially adding a batch.

There is no universal requirement that an experiment reach `analysis` or `complete` before its Samples can
be included. Check processing and QC yourself before using them in downstream comparisons.

## Launch and Output Scope

For a Notebook Session or Work Node, a project-only launch records that Project. An experiment-only launch
records the experiment and its Project when present. A launch supplying both must use an experiment whose
own `project_id` matches the chosen Project; explicit cross-project Sample membership does not change that
rule. Recreate preserves the session's recorded scope.

Select the actual inputs for the pipeline or interactive workload. Project metadata does not grant new
file mounts. Output paths and registration follow the workload's run/session contract, not a universal
`<instance>-projects` bucket layout. See [Custom Pipelines](custom-pipelines.md) and
[Notebook Sessions](../user-guide-compbio.md#notebook-sessions).

## Provenance and Snapshot History

Use the project's provenance and analysis controls to inspect recorded relationships. Registered inputs,
Pipeline Runs, outputs and notebook records provide the available lineage; unregistered shell work is not
reconstructed automatically.

Project snapshot listings and counts include both directly associated snapshots and snapshots whose
experiment belongs to the Project, counted once. That timeline rule does not expand every provenance query.
Snapshot visibility is also permission- and Organization-scoped.

The shared **Export Provenance** menu offers JSON, CSV and PDF reports. A report export is separate from
a visual screenshot of a DAG. Use [Data Exports](data-exports.md) for registered file contents and
[GEO Export](geo-export.md) for a SuperSeries metadata package.

## Status and Access

Project status values are `active`, `archived` and `complete`. A status label is not by itself a guarantee
that all APIs become read-only. Mutations depend on their route permissions and validations. Preserve the
record and its links when work is complete, and review provenance before removing associations.
