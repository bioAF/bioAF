# Custom QC Report Configuration

Custom pipelines can ship their own QC dashboard. The dashboard is driven by two pieces:

1. A **render config** (`qc_config_json`) attached to the pipeline version, describing which sections, metric labels, formats, and thresholds to display.
2. A **`qc_metrics.json`** file that the pipeline writes to `/outputs/` at run time, supplying the actual metric values.

This guide covers the config schema, how to wire your script up to it, and the iteration loop.

For a walkthrough of authoring custom pipelines themselves, see [Custom Pipelines](custom-pipelines.md).

## How It Fits Together

When a custom pipeline run completes, bioAF:

1. Looks up the pipeline version's `qc_template` and `qc_config_json`.
2. If `qc_template = custom`, reads `/outputs/qc_metrics.json` from the run's GCS prefix.
3. Renders the dashboard generically: sections from the config, values from the metrics file, formats and threshold colors from the config.

The render config travels with the pipeline version. Editing it produces a new version, so old runs always render the way they were generated even if you later evolve the layout. Inside `bioAF` the dashboard row also snapshots the resolved config at generation time, so this guarantee survives even if the pipeline is later deleted.

## Setting It Up

In the **New Version** form, expand the **QC dashboard config** panel.

1. Set **QC template** to `custom`.
2. Paste a JSON object into the **QC config JSON** textarea. The editor validates it client-side -- it must be a JSON object (not an array or scalar).
3. Save the version.

If you skip this step, the dashboard will not be generated for runs of that version (the pipeline can still emit `qc_metrics.json`, but nothing renders it).

## Config Schema

```json
{
  "template": "custom",
  "sections": [
    { "id": "hero", "layout": "hero", "metrics": ["duration_seconds", "input_bytes"] },
    { "id": "execution", "title": "Execution", "layout": "grid", "metrics": ["exit_code", "input_present"] }
  ],
  "metrics": {
    "duration_seconds": { "label": "Run Duration", "format": "decimal", "thresholds": { "good": "<10", "warn": "<60" } },
    "exit_code":        { "label": "Exit Code",     "format": "integer", "thresholds": { "good": "==0" } },
    "input_present":    { "label": "Input Present", "format": "integer", "thresholds": { "good": "==1" } },
    "input_bytes":      { "label": "Input Bytes",   "format": "integer" }
  },
  "charts": [],
  "plots": []
}
```

Top-level keys:

- **`template`** -- always `"custom"` for custom pipelines.
- **`sections`** -- ordered list of dashboard sections. Each section references metric keys by name; values pulled from `qc_metrics.json` with that key are rendered in the section's layout.
- **`metrics`** -- map of metric key -> display spec. Only metrics referenced by a section are rendered, but you can declare extras for documentation.
- **`charts`** -- reserved for future use; leave as `[]` for custom pipelines.
- **`plots`** -- reserved for future use; leave as `[]`.

### Section Layouts

| `layout`     | Renders as                                                  |
| ------------ | ----------------------------------------------------------- |
| `"hero"`     | Centered, large-font row at the top of the dashboard.       |
| `"grid"`     | Standard 4-column grid of metric cards. The default layout. |

Sections with no resolvable values (every metric is `null` / missing) are skipped automatically, so it is safe to declare optional sections.

### Metric Specs

Each entry in `metrics` accepts:

- **`label`** (required) -- the display name shown above the value.
- **`format`** (optional, defaults to `"raw"`) -- formatter applied to the value.
- **`thresholds`** (optional) -- color rules that drive the green / yellow / red status of the card.

#### Format Values

| `format`            | Input                | Rendered as          |
| ------------------- | -------------------- | -------------------- |
| `integer`           | `5234`               | `5,234`              |
| `decimal`           | `3.14`               | `3.14`               |
| `percent_decimal`   | `0.85`               | `85.0%`              |
| `percent_pct`       | `85.0`               | `85.0%`              |
| `bp`                | `150`                | `150 bp`             |
| `raw`               | anything             | `String(value)`      |

If the value is `null` or missing the card renders as a dash and is treated as neutral.

#### Threshold Rules

`thresholds` accepts up to two rules: `good` and `warn`. Each is a comparison string against the metric value:

- Operators: `>=`, `<=`, `>`, `<`, `==`
- Right-hand side: number literal

Evaluation order: `good` first; if it doesn't match, `warn`; otherwise the card is rendered red. Examples:

```json
"thresholds": { "good": ">=0.8", "warn": ">=0.5" }   // 0.85 → green, 0.6 → yellow, 0.3 → red
"thresholds": { "good": "<5",    "warn": "<10"  }    // mito %: lower is better
"thresholds": { "good": "==0" }                       // exit code: only 0 is green
"thresholds": { "good": "==1" }                       // boolean flag: 1 is green, anything else is red
```

If `thresholds` is omitted, the card is always neutral (gray).

## Emitting `qc_metrics.json`

Your pipeline writes a flat JSON object whose keys match the entries in the config's `metrics` map. Anything not referenced by the config is ignored. Anything referenced but missing simply doesn't render.

Two optional keys get special handling:

- **`quality_rating`** -- one of `excellent`, `good`, `acceptable`, `pending_review`, `concerning`. Drives the colored badge at the top of the dashboard. If the pipeline doesn't emit one, the badge shows `Pending Review`.
- **`summary_text`** -- one-line summary shown above the metric grids. Markdown bold (`**...**`) is honored. If absent, bioAF generates a generic one-liner.

### Hello-World Example

The minimal end-to-end example: a bash pipeline that emits a few execution metrics.

**QC config JSON** (paste into the editor, with `QC template = custom`):

```json
{
  "template": "custom",
  "sections": [
    { "id": "hero", "layout": "hero", "metrics": ["duration_seconds", "input_bytes"] },
    {
      "id": "execution",
      "title": "Execution",
      "layout": "grid",
      "metrics": ["exit_code", "manifest_present", "input_present", "report_written"]
    }
  ],
  "metrics": {
    "duration_seconds": { "label": "Duration",        "format": "decimal", "thresholds": { "good": "<10", "warn": "<60" } },
    "input_bytes":      { "label": "Input Bytes",     "format": "integer" },
    "exit_code":        { "label": "Exit Code",       "format": "integer", "thresholds": { "good": "==0" } },
    "manifest_present": { "label": "Manifest",        "format": "integer", "thresholds": { "good": "==1" } },
    "input_present":    { "label": "Input File",      "format": "integer", "thresholds": { "good": "==1" } },
    "report_written":   { "label": "Report",          "format": "integer", "thresholds": { "good": "==1" } }
  },
  "charts": [],
  "plots": []
}
```

**Pipeline script** (note the `qc_metrics.json` write at the end):

```bash
#!/bin/bash
set -u

LOG=/outputs/test-script.log
QC_METRICS=/outputs/qc_metrics.json
START_EPOCH=$(date +%s)

INPUT=$(find /data -name 'test.txt' -type f | head -n1)
INPUT_PRESENT=0; INPUT_BYTES=0
if [ -n "${INPUT}" ]; then
  INPUT_PRESENT=1
  INPUT_BYTES=$(wc -c < "${INPUT}" | tr -d ' ')
fi

MANIFEST_PRESENT=0
[ -f /data/manifest.json ] && MANIFEST_PRESENT=1

# ...your real work here...

REPORT_WRITTEN=1
DURATION=$(( $(date +%s) - START_EPOCH ))

QUALITY="good"
if [ "${MANIFEST_PRESENT}" -eq 0 ] || [ "${INPUT_PRESENT}" -eq 0 ]; then
  QUALITY="concerning"
fi

cat > "${QC_METRICS}" <<JSON
{
  "duration_seconds": ${DURATION},
  "input_bytes": ${INPUT_BYTES},
  "exit_code": 0,
  "manifest_present": ${MANIFEST_PRESENT},
  "input_present": ${INPUT_PRESENT},
  "report_written": ${REPORT_WRITTEN},
  "quality_rating": "${QUALITY}",
  "summary_text": "Run completed in ${DURATION}s. Manifest: ${MANIFEST_PRESENT}, input: ${INPUT_PRESENT}."
}
JSON
```

After the run completes, open **QC Dashboards** (or the run detail page's QC tab) to see the rendered dashboard.

## Iterating on the Layout

Editing the QC config in the **New Version** form creates a new pipeline version, the same way editing the script does. This is intentional: layout changes are pipeline changes. Old runs render against their own snapshotted config, so you can refactor freely without breaking prior runs.

If you want to preview the layout without re-running a pipeline:

1. Edit the QC config on a new version.
2. Find an existing run of the previous version.
3. Click **Regenerate** on the QC Dashboards page. Regenerate uses the *current* pipeline-version config -- but only if you re-launch the run; existing rows keep their original snapshot. To force a re-render with new config, launch a fresh run of the new version.

## Common Pitfalls

- **Metric value is `null` or missing** -- the card doesn't render, and a section made up entirely of missing metrics is hidden. Check the run's GCS prefix to confirm `qc_metrics.json` actually got written and contains the keys you expect.
- **JSON parse error in the editor** -- the panel shows a red "JSON: ..." note inline. Trailing commas and unquoted keys are the usual culprits; the editor enforces strict JSON.
- **Threshold rule never matches** -- the comparison must be a single operator + numeric literal. Whitespace is fine; ranges (`"5..10"`) and string comparisons aren't supported.
- **`quality_rating` shows `Concerning`** -- the custom template's default rating when no `quality_rating` is emitted is `pending_review`; `concerning` only happens when the pipeline explicitly writes that rating. Most often this means an old run before you wired up the metrics file.
- **Renaming a metric breaks old dashboards** -- it doesn't; old dashboards render their snapshotted config. New runs need both the config and the script updated together. Submit them in the same new pipeline version to keep them aligned.
