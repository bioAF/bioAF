# Unreleased changes

Each pull request that ships a user-visible change drops one markdown file into
this directory. On the next merge to `main`, the release workflow assembles
every file here into the `RELEASE_NOTES.md` section for the new version, then
deletes them.

## How to add an entry

Create a new file in this directory. Any filename ending in `.md` works, as
long as it does not collide with another open PR. A safe pattern is your PR
branch name, e.g. `bmills-fix-qc-grid.md`.

The file is a markdown fragment. Use one or more bullets. Optional H3 subheads
group related entries when assembled. Example:

```markdown
### Pipeline runs

- Add a Results tab on the Pipeline Run detail page that embeds the QC
  dashboard and Plot Archive entries for the run.

### Fixes

- Stop emitting empty MultiQC plot grids on nf-core 1.20+ by scoping
  `ext.args = ' --export'` to the MULTIQC process in the generated
  `nextflow.config`.
```

## When you do not need an entry

If a PR is internal-only (CI tweak, test-only change, refactor with no user
impact, doc fix), apply the `no-changelog` label on GitHub instead of adding
a file. The changelog-check workflow respects that label.

## What gets generated

On release, the workflow:

1. Reads every `*.md` file under `changes/unreleased/` (this README excluded).
2. Concatenates them in filename order under a new `## v<calver>` header.
3. Prepends that section to `RELEASE_NOTES.md`.
4. Deletes the consumed files.
5. Commits, tags, and creates the GitHub Release.
