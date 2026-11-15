# Custom Pipelines

Custom pipelines let you author your own analysis logic in any language (bash, Python, R, Perl, etc.) and run it against tracked input data with full provenance. Each pipeline is a versioned, reproducible artifact: script, command, variables, and a pinned environment image. This guide walks through creating a pipeline, defining its runtime contract, and iterating on new versions.

For per-pipeline QC dashboards, see the companion guide: [Custom QC Report Configuration](custom-qc-config.md).

## When to Use a Custom Pipeline

Use a custom pipeline when the work doesn't fit the built-in nf-core catalog -- for example, a one-off transformation, a lab-specific QC script, an export step, or a wrapper around a tool that bioAF doesn't ship a template for. If you need long-term automation around an established assay (scRNA-seq, bulk RNA-seq), prefer a built-in catalog entry.

## Prerequisites

Before creating a pipeline, you need:

- A **Pipeline environment** -- a Docker image that has your interpreters, libraries, and tools installed. Manage these from **Pipelines > Pipeline Templates**. The environment must be in `ready` status before you can pin a pipeline version against it.
- (Optional) A **GitHub repo** registered with bioAF if you want the pipeline to clone code at run time instead of pasting a script blob. Add repos in the GitHub repositories section of **Workbench > Work Nodes**.

## Creating a Pipeline

1. Navigate to **Pipelines > Custom Pipelines** and click **Create Pipeline**.
2. Give the pipeline a name and (optionally) a description. The name is just a human label; the auto-generated `pipeline_key` (slug form of the name) is what shows up in run records.
3. Submit the form. bioAF opens the pipeline detail page with the **New Version** form already open.

The pipeline itself is a thin container -- the real configuration lives on its **versions**.

## Adding the First Version

Click **New Version** on the pipeline detail page. The version form has four sections.

### Code Source

Pick where your script lives:

- **GitHub repo** -- bioAF clones the registered repo into `/code/<repo-display-name>/` at run time. The clone becomes the working directory. Use this when your code is more than a few hundred lines or when you want commits to drive version bumps.
- **Code blob** -- paste the script directly. bioAF writes it to `/code/script` and runs your entrypoint command from there. Best for short scripts.
- **Inline command** -- no file is materialized; your entrypoint command runs as-is from `/data`. Good for one-liners.

### Entrypoint Command

The shell command to run after code is staged. Examples:

- Code blob with a bash script: `bash /code/script`
- GitHub repo with a Python entrypoint: `python main.py --input /data`
- Inline: `samtools view -c /data/sample/aln.bam > /outputs/count.txt`

The command runs from the working directory described above and inherits the container environment plus the `PARAM_<NAME>` variables defined below.

### Environment + Resources

- **Pipeline Template** -- pick a built `ready` environment version. Each pipeline version pins that environment version; later builds can create a new pipeline version through the environment cascade described below.
- **CPU / Memory** -- Kubernetes requests for the pod; choose values appropriate for the workload and available resources.
- **Log file path** -- optional `/outputs/...` path that the run page tails after completion. Without this, the run page shows pod stdout/stderr.

### Variables

Variables are launch-time parameters. Each row gets a name, type (`string` / `number` / `boolean`), default value, and required flag. At launch the user is prompted for non-default values; at run time bioAF delivers the resolved values two ways:

- As environment variables: `PARAM_<UPPERCASED_NAME>` (read these as values, not shell code)
- As a JSON file at `/data/params.json` containing `{ "var_name": "value", ... }`

Read whichever is more ergonomic for your script.

### QC Dashboard Config (optional)

Expand the **QC dashboard config** panel if your pipeline emits structured QC metrics and you want a dashboard for them. This is covered in detail in [Custom QC Report Configuration](custom-qc-config.md).

Click **Create Version** to save. Launchability depends on a ready pinned environment, version state,
permissions and provider capabilities.

## The Runtime Contract

When your pipeline runs, bioAF sets up a deterministic filesystem layout. Your script can rely on it:

- **`/data/`** -- input files staged here using the path `<project>/<experiment>/<sample>/<filename>`. Files attached at experiment scope appear under `/data/<project>/<experiment>/`; project-scoped files at `/data/<project>/`. The run page's launch dialog lets you pick which files to attach.
- **`/data/manifest.json`** -- maps file IDs to their staged paths plus project/experiment/sample metadata. Use this when you need anything richer than the path.
- **`/data/params.json`** -- launch-time variable values, plus `PARAM_<NAME>` env vars for the same data.
- **`/code/...`** -- your script or cloned repo.
- **`/outputs/`** -- output files collected here are uploaded to the configured storage backend and registered as `File` records on the run after the script exits. Subdirectories are preserved.
- **`/outputs/report/report.md`** or `report.html` -- if present, the run page renders it as the run's summary report.
- **`/outputs/qc_metrics.json`** -- if present and the pipeline version has `qc_template = custom`, drives the QC dashboard for the run.

Input names and parameter strings are kept literal during staging and command construction. The entrypoint
is intentional executable code, while a variable value is data. Register outputs only from this run's
output location; historical files in a shared prefix are not automatically outputs of the current run.

Stored Markdown/HTML reports use the shared safe-rendering path. Arbitrary scripts or active markup may
be removed or isolated, so do not make the report depend on unrestricted HTML execution.

The run pod runs as a non-root user. SIGKILL / exit code 137 is auto-detected as out-of-memory and surfaced on the run page.

## Launching a Run

1. From the pipeline detail page, click **Launch**.
2. The launch dialog asks for:
   - **Project + experiment** (optional). Setting these scopes the output files and gives the run page back-links.
   - **Input files** -- pick from any files visible to the project/experiment.
   - **Variable values** -- one input per declared variable.
3. Click **Launch**. bioAF creates a `pipeline_run` record, schedules a Kubernetes Job, and redirects to the run detail page where you can watch logs in real time.

The run progresses through `pending -> running -> completed` (or `failed`). If submission cannot be
confirmed, the run remains recorded as `failed` with `submission_failed`; check cluster state before retrying. On completion the output sync collects everything under `/outputs/`, registers files, runs the QC dashboard generator if applicable, and serves any report file on the run detail page.

## Iterating on a Pipeline

Pipeline versions are immutable. Editing the script, command, variables, environment, or QC config from the **New Version** form creates a *new version* of the pipeline -- the previous version stays runnable for reproducibility. Every run records the exact `version_number` it executed against, so old runs continue to make sense even after you've evolved the pipeline.

There are two version-creation paths:

- **Manual save** -- the **New Version** button. The form pre-fills from the latest version so you only edit what changed.
- **Environment cascade** -- a completed environment build can create the next integer-numbered version for pipelines whose latest active version uses that environment. The new version is tagged `version_trigger = environment_cascade` and pins the new environment build.

To stop a version from being launchable without losing it from history, click **Deprecate** on the version row. Deprecated versions still display on past runs but are filtered out of launch dialogs.

## Permissions

Custom pipeline access is controlled by the standard role/permission system:

- `custom_pipelines:view` -- list and view pipelines + versions
- `custom_pipelines:create` -- create pipeline definitions
- `custom_pipelines:edit` -- update pipeline metadata and create versions
- `custom_pipelines:delete` -- disable the associated catalog entry while preserving pipeline and run history
- `custom_pipelines:launch` -- launch runs

Built-in `admin` has all custom-pipeline grants. `comp_bio` has view/create/edit/launch but not delete;
`bench` has view/launch, and `viewer` has view. Custom roles use their explicit grants.

## Troubleshooting

- **Run fails immediately** -- check **Pipelines > Pipeline Templates**: the version selected in the **Pipeline Template** field must be `ready`, not `building` or `failed`.
- **Variables come through as `<unset>`** -- the variable name in the script's `PARAM_<NAME>` reference must match the declared variable name (uppercased). bioAF lowercases stored names, so `var1` becomes `PARAM_VAR1`.
- **Output files aren't showing up** -- only files written under `/outputs/` are synced. Files written elsewhere (e.g. `/tmp` or `/data/results`) are discarded when the pod terminates.
- **Run is OOM-killed** -- watch the run page for the OOM badge. Bump the **Memory** field on a new version.
- **QC dashboard is empty** -- see the QC config guide. Most often the pipeline isn't writing `qc_metrics.json` or its keys don't match the `metrics` map in the version's QC config.
