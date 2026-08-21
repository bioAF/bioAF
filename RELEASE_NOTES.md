# Release Notes

> Starting with the first release after v0.15.1, bioAF uses CalVer instead of
> SemVer. Version tags are `vYYYY.MM.N` (year, month, monthly increment), for
> example `v2026.5.0` for the first release in May 2026, `v2026.5.1` for the
> second, and so on. The format is three numeric segments by design: every
> deployed client (including pre-cutover `0.x` releases) accepts it as a
> valid install target, so the in-app Update button continues to surface and
> install the latest release across the cutover with no user action. The
> release date is recoverable from the GitHub release timestamp and the
> changelog section. Sections below v0.15.1 remain in their original SemVer
> format.

## v2026.8.7

### Pipeline runs

- A run nobody has reviewed yet is no longer treated as a failed request. Every
  run starts unreviewed and stays there until a reviewer files a verdict, but
  asking bioAF for that run's review came back as an error, so opening the
  Review tab on a fresh run recorded a failure in the browser every time and
  buried the failures that matter. bioAF now answers plainly that there is no
  review yet.
- If a run's review genuinely cannot be read, the Review tab says so rather than
  showing the run as one nobody has reviewed, and it still refuses to arm a
  second review over a verdict the reviewer cannot see.

## v2026.8.6

### Files

- Deleting a file now frees the storage it occupied. Until now deletion removed
  the file from bioAF and left every byte in the bucket, so a scientist who
  deleted a 40 GB BAM to reclaim space reclaimed nothing and went on paying for
  it. bioAF still keeps a permanent record that the file existed, who deleted it
  and when, and which runs used it, so no result loses its provenance.
- The confirmation before a delete says what actually happens: the file is
  erased permanently, and neither it nor anything that still needs it can be
  recovered.
- A file whose storage cannot be reached is not deleted at all, rather than
  vanishing from bioAF while its data survives. Deleting several at once reports
  how many were erased instead of claiming that none were.

## v2026.8.5

### Pipeline launches

- Say a run cannot start when its selection holds a sample with no input files,
  before Launch is pressed rather than after. The check itself is not new: the
  launch has always refused these, and the preflight did not run it, so a sheet
  carrying a row with an empty required column was reviewed and reported as fine.
- Name the samples that have nothing attached, and offer to drop them from the
  block itself. Previously that offer arrived only as a dialog after the launch
  had already been refused.
- Preview the sheet with the dropped samples already gone, so the sheet reviewed
  is the sheet submitted rather than one carrying rows the run will not contain.
- Ask again when the selection changes, so a sample added after the decision is
  never dropped by an answer given before it existed.

## v2026.8.4

### Pipeline launches

- Build every samplesheet from the pipeline's own `schema_input.json` instead of
  a fixed `sample,fastq_1,fastq_2`, so each pipeline gets the columns it actually
  declares, in a stable order, with only the ones bioAF can fill.
- Say a run cannot start **before** Launch is pressed, naming the columns that
  are missing, the samples they are missing for, and what would fix each one.
  Previously a mismatched sheet failed inside Nextflow minutes later, after a
  node had scaled up.
- Show the sheet a launch would submit, as a table and as raw CSV, on every
  launch. Any cell can be corrected in place for that run.
- Ask for the values bioAF cannot derive, one row per sample, with the
  pipeline's own allowed values and error text as the hint.
- Launch from any per-sample file a pipeline declares, not only sequencing
  reads, which unblocks pipelines whose input is a BAM, CRAM or image.
- Name the exact characters a pipeline's schema rejects and recommend a spelling
  it would accept, rather than reporting a present value as missing. Nothing is
  ever renamed on the scientist's behalf.
- Refuse to emit a sheet whose rows the pipeline could not tell apart, and fill
  the distinguishing column where bioAF holds the fact: `lane` from the file's
  own lane, and `run` from its archive run accession or flow cell.
- Where no value could resolve a repetition, say so and name the remedy instead
  of offering a field that cannot help. Two lanes of one flow cell are one
  sequencing run, and ampliseq takes one row per sample whatever is typed.
- Declare the samplesheet columns for a pipeline that publishes no contract, and
  bind each to a sample field, a read, a file type, a custom field or a fixed
  value. The declaration binds the run in front of you, and saving it for next
  time stays a separate, deliberate step.
- Save a samplesheet design and have it offered back on the next run, at
  experiment, project or organisation scope.

### Provenance

- Keep the samplesheet each run was given, exactly as submitted, together with
  who stated each value in it. It is a record of what ran, never a sheet rebuilt
  from today's data.
- Show that sheet with each row's asset identifier beside it, behind a toggle,
  so a misattribution can be checked by eye. The identifier is never submitted
  to the pipeline.
- Attribute a pipeline output to its sample by identity, falling back to the
  name the run actually emitted rather than the name the sample carries now. An
  output that names no sample attaches to the run instead of to every sample in
  it.

### Samples and files

- Record a file's sequencing identity as typed columns (flow cell, lane, read
  type, index sequence, archive run accession), read from the FASTQ header on
  ingest rather than guessed from the filename.
- Name the unit between a sample and its files a **Read Group**, the term the
  SAM specification and every aligner already use, and expose it per sample.
- Give every file a catalogue identity that survives a move or a rename.
- Retire a deleted file from view without removing it from the catalogue, so an
  exported dataset or a published provenance record never dangles.

### Results and QC

- Count the samples a run covered, rather than the sequencing files it read. A
  pipeline that runs no aligner publishes nothing that says which files belong
  to one sample, so a single sample sequenced over two lanes was reported as
  four samples, and its "reads per sample" was a per-file mean: half the real
  depth. bioAF now takes the roster from the samplesheet the run itself
  submitted.
- Fall back to the run's own samples where no sheet was recorded, so a run
  that predates that record still reports its real sample count instead of
  nothing. Where neither exists, report neither the sample count nor the read
  depth, and say so, rather than showing a file count under the word "Samples".
  Existing dashboards keep their values until they are regenerated.
- Say "1 sample" rather than "1 samples".

### Fixes

- Keep the part of a failed run's log that says what failed. The stored message
  was capped in a way that discarded the process name and exit status.
- Stop the dashboard being scrolled by content inside a hidden table, and keep
  absolutely positioned elements inside the page scroller.
- Render the parameter form for schemas that use `$defs`, and read vocabularies
  a schema declares numerically or in branches.
- Re-fetch a pipeline's samplesheet contract when its version moves.
- Say when a registry pipeline has no release available to install.
- Offer the 10x protocol only to pipelines whose own schema accepts it.
- Refuse a lab-document import URL whose host cannot be shown to resolve to a
  public address, instead of attempting the fetch.

## v2026.8.3

### Quality control

- Corrected the read count and sample count shown on QC dashboards. Both were
  derived by counting the quality-report entries a run produced, but sequencers
  write one entry per file rather than per sample: paired-end runs produce two
  files per sample, and a sample split across lanes produces more still. A
  single-cell run of one sample across four files was reporting four samples,
  and either double or half its true read depth depending on the pipeline.
  bioAF now works out the real samples from the alignment step and adds up each
  sample's files correctly.

  What changes when you regenerate a dashboard: single-cell runs show the
  correct read depth and sample count, and paired-end ChIP-seq and ATAC-seq runs
  show the correct sample count. Read depth on bulk RNA-seq, ChIP-seq and
  ATAC-seq runs is unchanged, as are all other metrics. Existing dashboards keep
  their current numbers until regenerated.

  This also matters for literature validation, where a paper's reported reads
  per sample is compared against the run: on single-cell papers that comparison
  was previously made against a figure that counted every read twice.

## v2026.8.2

### Quality control

- QC dashboards now work for every pipeline, not just the four bioAF had been
  taught individually. Previously, running anything outside single-cell RNA-seq,
  bulk RNA-seq, ChIP-seq or ATAC-seq produced a dashboard with no numbers on it,
  because nobody had written a reader for that pipeline's output yet. bioAF now
  reads the standard quality report that every nf-core pipeline produces, so a
  methylation, variant-calling or any other pipeline you install shows real
  metrics straight away, with no work needed on our side first.

- Quality dashboards for these newly covered pipelines also list every other
  measurement their tools reported, labelled with the tool that produced it, so
  nothing a pipeline measured is thrown away just because bioAF does not have a
  standard name for it yet.

### Fixes

- Fixed missing sequencing numbers on single-cell RNA-seq quality dashboards.
  Read count, sample count, GC content, read length and duplication were blank
  on runs processed with recent versions of the underlying reporting tool, which
  had changed the layout of the file bioAF reads them from. The cell-level
  numbers on those dashboards (cell count, genes per cell, saturation, mapping
  rates) were never affected. Regenerating a run's QC dashboard fills the missing
  values in.

- A pipeline bioAF has no tailored quality template for is no longer read as
  though it were a single-cell run, which previously applied single-cell
  expectations, chart choices and quality wording to unrelated pipelines.

## v2026.8.1

### Security

- Updated the behind-the-scenes software libraries bioAF depends on, clearing
  every outstanding advisory raised by automated dependency scanning (29 in
  total, across the backend and the web interface). Routine security upkeep:
  how bioAF looks and works for you is unchanged.

- Hardened the built-in PDF viewer used for literature papers and lab
  documents. PDFs can carry embedded scripts, and the viewer previously ran
  them. Since these files come from outside sources (publishers, or whatever a
  colleague uploads), that was a way for a malicious document to run code in
  your browser session. The viewer no longer runs anything a PDF asks it to.
  Papers and documents display and page through exactly as before.

### Fixes

- Fixed a database upgrade step that could fail on installs where the bioAF
  database shares a server with other schemas, leaving the upgrade part-way
  through. Existing installations are unaffected.

## v2026.8.0

### Quality of life improvements

- **A failure now says so.** Across the app, a request that failed used to render
  as an empty list, a zero, or a confident wrong answer. Failed loads state what
  went wrong in plain language and offer a retry, dashboard widgets no longer
  report an outage as a number, and a form whose settings could not be read will
  not let you save component defaults over them. A run list that could not load
  no longer reads "No pipeline runs".

- **Cost history is recorded.** The billing sync ran on every Cost Center page
  load and discarded everything it wrote, so `cost_records` was permanently
  empty and the dashboard's spend trend had nothing to show. The sync now
  commits, runs shortly after startup rather than a day later, and is serialised
  per organisation so two overlapping syncs cannot double a day's spend.

- **Destructive actions confirm in proportion to what they destroy.** Every
  confirmation names what will be deleted and what survives, the strongest one
  requires a typed phrase, and the browser's own `confirm`, `alert` and `prompt`
  are gone from the product in favour of in-app dialogs that can be read,
  cancelled and styled.

- **The app can be driven from the keyboard.** A visible focus ring throughout,
  Escape closes any dialog that closes on a backdrop click, focus is trapped
  inside dialogs and returned to the control that opened them, 38 mouse-only
  controls gained a keyboard path, upload drop zones are reachable without a
  mouse, and 291 table headers are associated with the cells they label for
  screen readers.

- **Pipeline runs sort and page over the whole list.** Sorting is done by the
  server rather than over the rows already on screen, and the page size is
  selectable. The fleet view refreshes without a reload, an in-flight run's
  duration ticks, and a run that failed before Nextflow wrote a trace now shows
  the reason it failed instead of nothing.

- **Notification preferences are honoured.** In-app notifications respect the
  toggle that claimed to control them, email delivery is opt-in and routed
  through the configured SMTP server rather than requiring a rule no screen
  could create, and a partial save no longer wipes stored settings.

- **Smaller frictions.** Search no longer fires a request on every keystroke,
  launching a pipeline keeps the experiment you arrived with, the Plot Archive
  stops indexing report furniture as if it were a plot, and the boot screen
  distinguishes "still starting" from "failed to start".

### User interface improvements

- **Dark mode.** A real theme rather than a shim: colours resolve through
  semantic tokens, the saved theme is applied before first paint so there is no
  flash of the wrong one, and 444 tinted panels that painted light on a dark
  page are fixed.

- **One shell for every page.** The sidebar and header are mounted once by the
  layout instead of being re-declared by each page, which also fixes the
  loading splash, the auth redirect and the nav state drifting between screens.

- **Shared button, card and dialog.** The app has a `Button`, a `Card` and a
  `Modal`. 130 hand-spelled primary and danger actions, 74 hand-spelled panels
  and every hand-rolled dialog overlay now use them, so the same control is not
  re-invented per page. Buttons gained a focus ring and an explicit type, which
  stops a control that only opened a picker from submitting the form around it.

- **Navigation.** Literature and Validation Studies moved into Lab Knowledge,
  every nav label is now the heading of the page it opens, breadcrumbs appear
  across the app, and the sidebar has section icons and a collapsed icon rail.

- **The browser tab names the page.** Every route reported "bioAF", so tabs,
  history entries and bookmarks could not be told apart. Each page now names
  itself, on a full load and on in-app navigation.

- **Every page says what it is for** before it has any data to show, and a slow
  load shows the shape of what is coming rather than a spinner.

- **The Cost / spend trend chart can be read.** Each bar shows its date and
  total on hover or with the arrow keys, and the day of the month sits under the
  bar. The same figures are available to a screen reader as text.

- **A phone can reach the whole table.** Tables that were clipped on narrow
  screens scroll to their remaining columns, the layout uses the full width, and
  the header search is a usable control rather than a 26px sliver.

- **Consistency.** Status colours come from one place instead of per-page maps,
  a missing value renders as one placeholder defined once, and the terminology
  is unified on Templates, Provenance and Workbench Images.

### Validation studies and literature

- **Pick the samples a reproduction runs on.** The Level-3 gate now shows a
  sample manifest resolved from the study's accessions, with test and reference
  groups pre-grouped from the design, manual add, and a free-text fallback when
  the manifest cannot be retrieved.

- **A study that did not get all its samples stops instead of guessing.** Picked
  accessions are resolved to real sample identifiers after fetching, and a study
  whose samples are incomplete parks in a `samples_mismatch` state having spent
  no compute. The detail page names the samples that were not fetched and offers
  either running with what was retrieved or stopping.

- **Studies are named after the paper they reproduce** rather than by number,
  on the list, the detail header and the breadcrumb, with the numeric id kept as
  a secondary qualifier.

- **AI Lit Review reports progress** per source while it runs, with elapsed time
  and a control to stop watching, and library papers are searchable from the
  header.

## v2026.7.2

### Literature validation (Level 3)

- Add a `partially_reproduced` classification, surfaced as "Partially Reproduced", for a paper whose
  differential finding reproduced in part: the overlap with the paper's own result set is
  statistically real (enrichment clears) but directional recovery is below the agreement threshold.
  It always holds for a human, and it narrows `inconclusive` back to "we genuinely cannot tell"
  rather than lumping in "we clearly reproduced part of it."
- Support matched-pairs (paired / blocked) designs in the differential-expression reproduction.
  A per-sample subject/donor label captured at the reproduction gate makes the analysis run
  `~ subject + condition` (instead of the unpaired `~ condition`), cancelling donor-to-donor
  baseline variance and sharply raising power for paired studies. A confounded or unbalanced pairing
  is rejected at the gate before any compute spend.
- Auto-retry a transient data-acquisition failure (an ENA/SRA outage, connection-refused, or timeout)
  with bounded exponential backoff instead of parking the study in a terminal error that needs manual
  intervention. A genuinely unavailable accession is still classified `missing_data` immediately.

## v2026.7.1

### Quality control

- QC dashboards now analyze ChIP-seq and ATAC-seq pipeline runs, reporting peak counts,
  FRiP, and (for ChIP-seq) NSC/RSC strand cross-correlation, plus TSS enrichment for
  ATAC-seq. Bulk RNA-seq QC coverage is more thorough as well.

### Provenance

- Provenance reports can now be exported as CSV, in addition to JSON and PDF, from
  projects, experiments, and pipeline runs.

### Fixes

- Peak-calling pipelines (ChIP-seq, ATAC-seq) now launch correctly: the selected
  reference genome is passed through to nf-core and the MACS genome size is derived
  automatically, so runs no longer fail on a missing `--genome` or `--macs_gsize`.
- Importing a multi-run SRA/ENA study via fetchngs no longer crashes data ingest, and
  fetched FASTQ files now attach to their samples.
- Fixed a cluster cost leak where Kubernetes system pods could pin the pipelines node
  pool open and keep it from scaling to zero when idle; the pool is now tainted so it
  releases when unused.
- Literature full-text retrieval now resolves articles through Europe PMC, and a
  text-sanitizer bug that could strip article prose has been fixed.

### Coming soon

- Groundwork for an upcoming literature validation feature.

## v2026.7.0

### Conversational assistant

- Add an AI assistant you can chat with to run bioinformatics pipelines without writing Nextflow.
  Describe what you have and what you want, and it resolves the right experiment or sample, recommends
  and installs the appropriate nf-core pipeline, sets up experiments and samples, imports data by
  accession, launches the run, and explains the results, all in plain language. It is opened from an
  icon in the header and is available to roles that hold the `assistant:use` permission.
- Every consequential action (installing a pipeline, creating an experiment or sample, launching a run)
  is presented as a plan you confirm before anything runs, and launching shows a "this will spend
  compute" warning. The assistant only ever acts within your own role's permissions, and content it
  reads back (sample metadata, imported accession data, QC text) is treated as data, never as
  instructions.
- Read back a run's QC metrics and get a plain-language explanation of what the results mean, in chat.
  Users who hold the AI review permission can also ask the assistant to run a full, saved agent review
  of a run.
- Ask "what did I run this session?" to get a log of the actions taken in the conversation.
- Conversations are saved: reopen and resume any past chat from the assistant's History.

### Audit

- Actions taken through the assistant are attributed to you in the audit log and marked "via assistant",
  so a reviewer sees who took each action and that the agent was used.

### Fixes

- Tear down cellxgene publishes that never become ready and surface the teardown failure instead of
  leaking the resources.

## v2026.6.20

### File upload

- Suggested filenames are now opt-in. When you upload files, bioAF still
  recommends names based on your naming profile, but your original filenames are
  kept unless you choose to accept a suggestion. Accept them one at a time, or
  use "Accept all" / "Dismiss all" to handle the whole batch at once.

## v2026.6.19

### Run bioAF on AWS

- bioAF can now be deployed on Amazon Web Services (AWS) as well as Google
  Cloud. On AWS, notebooks, work nodes, and custom environment images all work
  the same way they do on Google Cloud, including saving your notebook outputs
  back to storage when a session ends.

### Work nodes

- You can now start a work node straight from an experiment, even if that
  experiment isn't part of a project yet. Before, a work node always needed a
  project, so a standalone experiment couldn't use one.

### Environments

- When you build a conda environment, bioAF now checks the definition as soon as
  you save it. If it isn't a valid conda environment file (for example, a
  Dockerfile pasted in by mistake), you get a clear message right away instead
  of a confusing failure partway through the build.

## v2026.6.18

### Cloud platforms

- bioAF can now run on **Amazon Web Services (AWS)**, alongside the existing
  Google Cloud option. A new one-command installer (`install-aws.sh`) sets up
  your AWS account and a server for you, the same way the Google Cloud installer
  does. File storage and pipeline runs work on AWS today; a few features (the
  single-cell dataset viewer, interactive notebooks, and SSH work nodes) are
  still being finished and will arrive in upcoming releases. Existing Google
  Cloud installs are unaffected.

### Fixes

- Pipeline run logs no longer display as a raw escaped blob (`b"...\n..."`) on
  in-progress runs. Logs now render as clean, line-wrapped output regardless of the
  Kubernetes client version in use.

### Behind the scenes

- Structural, behavior-preserving changes to how bioAF integrates with its cloud
  provider (storage, Kubernetes, container image builds, and credentials). These
  changes are invisible to end users and do not alter current behavior. They improve
  reliability and prepare bioAF for upcoming AWS-based deployment options alongside GCP.

### Security

- Refreshed several of the behind-the-scenes software libraries bioAF depends on
  to their latest secure versions, clearing a batch of security advisories raised
  by automated dependency scanning. This is routine security upkeep: how bioAF
  looks and works for you is unchanged.

## v2026.6.17

### Internal

- Added a backend-neutral storage-URI primitive to the storage adapter:
  `build_uri(bucket, key)` mints a URI for an explicit bucket/key pair
  (`gs://` on GCS, `file://` on NFS) and `parse_uri(uri)` is its inverse, both
  with round-trip tests. Migrated every call site that hardcoded
  `f"gs://{bucket}/{key}"` onto it, draining the BAL layering guard's `gs://`
  scheme-literal allowlist from 28 files to 0. Behavior-preserving: the minted
  URI is byte-identical on a GCS install, so there is no change for existing or
  new installs. This removes the last `gs://` literals from the service layer,
  a prerequisite for supporting non-GCS storage backends.

### Fixes

- `./bioaf setup` no longer silently drops flags when it re-execs under
  `sg docker` to activate docker-group membership. Previously only `--version`
  survived the hop, so `--prefill` and `--local-build` were discarded on any
  host whose shell did not yet have an active `docker` group (the common
  fresh-VM case), which left the GCP project/region/zone prefill unapplied and
  the setup wizard blank. The re-exec now preserves the full original argument
  list.
- `./bioaf setup --local-build` now applies the prefill already present on the
  host (`~/.bioaf-prefill.yaml`, then `~/.bioaf/prefill.yaml`) without requiring
  an explicit `--prefill`, so building from local source still pre-populates
  `platform_config`.

- The Service Health widget on the Dashboard shows its status dots again. A
  narrowed Tailwind config was scanning only `app/` and `components/`, so the
  green (healthy) and yellow (degraded) dot colors, which live only in the shared
  status-style module under `lib/`, were stripped from the built CSS and rendered
  invisible. Tailwind now scans all of `src/`, so all health dots appear.

## v2026.6.16

### Fixes

- Single-cell viewer (cellxgene) instances now launch on installs that use the
  default (metadata) credential mode. Previously the viewer pod required a
  service-account key file that those installs do not have, so it never started
  and the launch timed out; the pod now authenticates via Workload Identity
  (the runner service account), and the key file is only used on installs that
  store one.
- Single-cell viewer (cellxgene) deployments no longer fail to launch or tear
  down after the compute cluster is rebuilt. The adapter now refreshes its
  Kubernetes connection when the cluster's address changes, instead of holding
  onto a stale connection until the backend is restarted.

## v2026.6.15

### Internal

- Refactored the Literature REST API for maintainability with no change to the
  HTTP surface. The 37 Pydantic request/response schemas that were declared
  inline now live in the shared `app/schemas/literature.py` layer, and the
  single 1,854-line router module is split into an `app/api/literature/` package
  with one sub-router per sub-domain (papers, comments, sources, searches, and
  so on). Request and response shapes, routes, and behavior are unchanged; a
  golden route-table test asserts the API surface is identical.

## v2026.6.14

### Maintenance

- Removed dead and superseded code paths with no change to app behavior: the
  legacy pre-ADR-033 package-management API (`/api/packages`) and environment
  reconciler, an unused budget pre-flight service, two orphaned manifest-ingest
  helper services, and two unused frontend components (`LoadingState`,
  `SuperSeriesExportModal`). Compute-environment package changes continue to be
  made by editing the environment's Dockerfile/conda definition and creating a
  new version.

## v2026.6.13

### Infrastructure

- Failed infrastructure deploys now raise a "Deployment failed" notification.
  Previously the notification had no emitter and never fired on a real Terraform
  apply failure.
- Consolidated Terraform execution onto a single owner (`TerraformExecutor`).
  Removed the legacy per-component Terraform "configure" flow, which produced
  no-op plans, and its standalone component detail page. Enabling and disabling
  components on the Infrastructure > Components screen is unchanged.

### Fixes

- Notebook and RStudio container image builds are reliable again. The image was
  floating on `:latest` everywhere, which left its R too old for current
  packages and broke the build. Pinned the base image, R (4.4.3), Bioconductor
  (3.20), the CRAN snapshot, and the Python packages, and added the OpenBLAS
  runtime so the single-cell/Seurat stack installs cleanly.

## v2026.6.12

### Fixes

- Service-account-key GCP credentials now work. When `gcp_credential_source`
  was set to `service_account_key`, the stored key was read without being
  decrypted, so credential loading failed and silently fell back to the VM's
  default service account. The key is now decrypted at read time and actually
  used. Installs using the default `vm_default` credential source are unaffected.

### Internal

- Consolidated all `platform_config` access behind `PlatformConfigService`
  (`get` / `get_many` / `set`, plus a new no-decrypt `export_all` for config
  backups). Removed ~140 raw SQL statements across 44 modules. An architecture
  guard test now fails the build if any module other than the service or the
  key-rotation CLI runs raw `platform_config` SQL, keeping the encrypt/decrypt
  boundary for sensitive keys in one place.

## v2026.6.11

### Pipeline runs

- Fix a cross-sample file-contamination bug: when a selected sample had no
  linked input files, the launch used to back-fill every such sample with the
  entire experiment's FASTQ list, so one sample's process received another
  sample's reads (and duplicate renamed + raw copies). On large data this ran
  for hours before the job was killed. FASTQ-consuming pipelines (nf-core,
  rnaseq/scrnaseq) now reject samples with no input files and report exactly
  which samples are missing them.
- When some selected samples have no files, launching (and reproducing) a run
  now offers to drop those samples and continue with the rest, instead of
  failing outright.
- The Provenance tab on a Pipeline Run now lists each input file by project,
  experiment, sample, and filename instead of showing bare numeric file IDs.
- A previous pipeline run's output files are no longer fed back in as inputs to
  the next run. Run inputs now use raw uploads only by default; a new
  "include derived inputs" option opts back in. This stops the dataset from
  compounding every run (which had been causing runs to balloon and get killed).
- Pipeline output files are now associated with the specific sample they belong
  to, instead of being linked to every sample in the run. Aggregate outputs
  (e.g. MultiQC) that don't belong to one sample are still linked to all of them.

### API

- Service and adapter errors now use a typed domain-exception hierarchy with a
  structured `{detail, code}` response envelope (some errors also include a
  `details` object). As part of this, several internal (non-`/v1/integrations`)
  endpoints now return `404` or `409` where they previously returned a generic
  `400`, when that status is the semantically correct one (not found, invalid
  state, or conflict/quota). The public Integration API surface is unchanged.

### Documentation

- Aligned README and `docs/` terminology with the canonical glossary (Agent
  Review, Work Nodes, Notebook Sessions, QC Dashboards, Audit Log, Reference
  Dataset, Naming Profile, Segment, and related terms).

### Internal

- Consolidated frontend status-badge rendering into a single entity-keyed
  registry (no visual change).
- Removed verified dead code and stale phase-era mock-data and backfill scripts.

## v2026.6.10

### Architecture

- Recontracted the BioAF Adapter Layer (BAL) so compute and storage backends are
  genuinely swappable ([ADR-065](decisions/ADR-065-bal-normalized-contract.md),
  superseding ADR-020). Every adapter method now returns a typed, backend-neutral
  normalized model, each backend declares a capability set that gates optional UI
  and is enforced server-side, and a committed import guardrail keeps cloud and
  Kubernetes SDKs out of application code. GKE, GCE, GCS, Secret Manager,
  Pub/Sub, Cloud Logging, IAM, and BigQuery billing now all route through BAL
  providers. Behavior on a GCP install is unchanged.
- Added a real NFS storage backend behind the storage provider, and a proxied
  file-upload path used automatically on backends without signed-URL upload.

### Pipeline runs and notebooks

- The Pipeline Run and Notebook session detail pages now show a generic
  "Provider details" disclosure for backend specifics (job/pod/namespace) instead
  of Kubernetes-only labels, so the views read the same regardless of compute
  backend.

### Fixes

- Optional controls (cost columns, cluster autoscale and spot toggles, SSH
  connect, cellxgene, work nodes, signed-URL upload) are now hidden when the
  active backend does not support them, rather than failing when used.

## v2026.6.9

### Lab Knowledge

- New top-level Lab Knowledge section with three areas: Documents, Glossary,
  and Decision Records. Everything is org-scoped, permission-gated, audit-logged,
  and surfaced in global and quick search.

#### Lab Documents

- Store operational and institutional documents (manuals, policies, SOPs) with
  controlled tags, upload-new-version history, and archive/restore. Distinct
  from experiment-linked files in Data & Files.
- Add a document either by uploading from your device or by having the server
  pull it from a public URL, matching the Reference Data add flow.
- Open a document to read it inline (paginated PDF viewer, plus image and text
  previews; other types offer a download), and add notes to it, the same way
  comments work on literature papers.

#### Lab Glossary

- Maintain a shared glossary of lab-specific terms with definitions, aliases,
  categories, and context. Add terms manually, import a CSV/TSV, or run an AI
  scan (from an experiment, a document, or platform-wide) that uses the org's
  active LLM provider.
- The "from an experiment" scan reads exactly the material the AI Experiment
  Review uses (experiment fields, samples, pipeline runs, and QC), grounding the
  proposed terms in real lab work instead of an abstract topic.
- The "from a document" scan picks its source by searching, spanning both Lab
  Knowledge documents and Data & Files files, instead of typing a database id.
- Every AI proposal and CSV import goes through human review before it is
  committed. A banner shows when proposals are awaiting review and opens the
  review flow; rejected proposals are remembered so future scans can flag them.

#### Scientific Decision Records (SDRs)

- Capture significant scientific decisions as numbered records (SDR-001, ...)
  with a decision, justification, owner, category, and a draft -> active ->
  flagged-for-review status workflow, including supersession links between
  records.
- Date-based re-assessment triggers flag a record for review on its due date
  and notify the owner, with a one-time 7-day advance warning.

#### Data & Files

- Searching Data & Files now also surfaces matching Lab Knowledge documents
  (a lab document is still a file-like thing), each badged "Lab Document" with a
  link to its detail page. Visible only to users who can view lab documents.

### Fixes

- Lab document uploads now use an origin-aware resumable upload session, fixing
  a "Failed to fetch" error when sending the file to storage from the browser.

## v2026.6.8

### Navigation

- The left navigation can now be collapsed to a slim rail and re-expanded
  from a toggle in its header. The collapsed state persists across reloads
  in `localStorage` under `bioaf-sidebar-collapsed`.
- Only one section in the sidebar can be expanded at a time. Expanding a
  second section automatically collapses the first, and the auto-expand
  on navigation follows the same rule.
- The sidebar brand block now matches the main top bar's height so the two
  align on a single baseline.
- Adds the bioAF logo to the sidebar header next to the "bioAF" wordmark
  (and on its own when the sidebar is collapsed), with the "Comp Bio
  Automation Framework" tagline beneath the wordmark on a single line.

## v2026.6.7

### Infrastructure

- Surface the GCE machine type in the Infrastructure > Components cluster
  configuration dropdowns and add e2-family options to the Interactive Pool.
  The pool was previously locked to the n2 family, which has been repeatedly
  stocking out in us-central1 for 8-vCPU scale-ups. Each option now reads
  `<machine-type> - <cpu/memory> - <description>`, and the e2 variants are
  tagged "(high availability)" because the e2 family spills over onto
  whichever generation of host has room. e2-standard-8 is the new
  recommended interactive size.
- Default the interactive node pool's machine type to e2-standard-8 (was
  n2-standard-4). e2-standard-8 has the same shape as n2-standard-8 (8 vCPU
  / 32 GB) but is allocated against any compatible host generation, so it
  almost never stocks out. The larger shape also unlocks Medium notebooks
  on a fresh install without operator intervention. Migration 093 updates
  the platform_config row only when it still holds the prior default;
  operators who already picked a different machine type keep their choice.

### Fixes

- Fix notebook launches failing with 401 Unauthorized after a fresh install
  or compute teardown + redeploy. The out-of-cluster Kubernetes client was
  setting the bearer token via `Configuration.api_key`, which the current
  `kubernetes-python` release silently drops when no OpenAPI security scheme
  references it. Every request went out anonymously and GKE responded 401.
  The notebook, cellxgene, and compute adapters now install the
  `Authorization` header on the `ApiClient` directly via `set_default_header`.
- Update the three raw-httpx callers (notebook session sync, notebook
  loadBalancer-IP poller, cellxgene loadBalancer-IP poller) to read the
  bearer token from `ApiClient.default_headers["Authorization"]` instead of
  `Configuration.api_key`. The previous reads would `IndexError` once the
  setter stopped populating `api_key`.
- Invalidate the cached Kubernetes API client for the notebook adapter when
  `platform_config` reports a different cluster endpoint or CA cert than
  what the cached client was built for. Previously a cluster teardown and
  redeploy would not take effect until the 45-minute GCP access token TTL
  elapsed, so notebook launches in that window kept hitting the old (dead)
  cluster and 401ing.

### Notebooks + Work Nodes

- Backend now classifies failed sessions with a `failure_reason` + human-readable
  `failure_message`, so the UI can distinguish "Resource Failure" (GCE zone
  exhausted, image pull failed, OOM, quota exceeded) from a generic "Failed".
  Sessions also carry a `requested_disk_gb` field set at launch from the pool
  or VM boot disk. Notebook + work-node responses now expose the
  associated `project` (in addition to the existing `experiment` for notebooks)
  so the table can render a single "Linked to" column.
- The session-list endpoints accept a `bucket` query parameter:
  - `bucket=active` returns only non-terminal sessions (pending, starting,
    running, idle, stopping).
  - `bucket=recent` returns sessions created in the last 24h, any status.
  - `bucket=all` (or omitted) returns everything.
  Unknown bucket values are rejected with HTTP 400.

## v2026.6.6

### Workbench

- Collapse the Work Node launcher from a 6-step wizard into a single
  Configure step plus Review, matching the Notebook launcher: machine
  profile, environment, Link to (Experiment / Project toggle + dropdown),
  input files, then GitHub repos. Machine selection uses 7 curated
  profile cards (5 CPU tiers plus GPU and high-memory defaults), with
  an Advanced expander that still surfaces the full GCE catalog.
- Surface the input file picker inline as soon as an experiment is
  selected, in both the Notebook and Work Node launchers. The old
  "Select files (N available)" link sat below the modal viewport and
  was easy to miss.
- Replace the legacy "Include FASTQ and BAM files" checkbox with a
  filter chip bar on the file picker: Defaults (10x cellranger trio
  plus h5ad), H5, CSV/TSV, Reports, FASTQ, BAM, Other. On open, only
  Defaults is active and matching files are pre-checked, so the common
  scRNA path needs zero clicks.
- Close the Work Node launch dialog immediately on Launch click and
  show a provisioning banner on the Work Nodes page. Errors surface
  in the banner with a Dismiss control. Removes the "is this hanging?"
  pause where the only feedback was a subtle "Launching..." label
  inside the dialog.
- Prompt before launching a Notebook or Work Node without any input
  files. Work Node additionally warns when GitHub repos are configured
  but none are linked to the run.

## v2026.6.5

### Compute

- GKE node pools (default bootstrap, pipelines, interactive) now use
  `pd-standard` disks instead of GKE's default `pd-balanced`. This
  matches the system and pipeline-head pools, which were already on
  `pd-standard`. The change cuts boot-disk cost by ~60% per node and,
  more importantly, removes the pipeline and interactive pools from
  the `SSD_TOTAL_GB` regional quota: a fresh install previously
  consumed 200 GB of SSD quota at idle (one pipelines node + one
  interactive node at GKE's 100 GB pd-balanced default), and at peak
  could blow past the 300 GB default limit during autoscaler retries.
  Scratch I/O for nf-core pipelines and notebook sessions is dominated
  by GCS round-trips and memory, so the throughput difference is not
  observable in practice.
- The bioAF app VM created by `install-gcp.sh` now provisions its
  boot disk as `pd-standard` instead of `pd-ssd`. Postgres on the app
  VM is far below the workload where SSD makes a measurable
  difference, and this drops the install's SSD footprint to zero.
- The optional Meilisearch component's 20 GB data disk moves from
  `pd-ssd` to `pd-standard`. At bioAF's scale the entire search index
  fits in RAM after warmup, so disk latency only matters for cold
  reads and indexing. Worth revisiting if Meilisearch is ever wired
  into the user-facing search path and a real workload shows up.
- Existing installs adopt the GKE pool changes by going to
  Infrastructure > Components and clicking Teardown, then Deploy.
  Teardown destroys the cluster and its node pools but leaves storage
  buckets and your data untouched; Deploy rebuilds the cluster with
  the new pd-standard disks. Total compute unavailability is roughly
  20-25 minutes; Postgres on the app VM is not touched at all. The
  in-app "Check for Updates" / "Apply Updates" flow is additive-only
  by design and will report the node pool replacement as a destructive
  change without applying it, which is correct: the redeploy path is
  the right tool for this kind of immutable-field change. The app VM
  boot disk change is install-time only; existing app VMs keep their
  `pd-ssd` boot disk until manually migrated.

## v2026.6.4

### Infrastructure menu

- Removed the Infrastructure > Compute page along with the Job Browser and
  Quotas pages it linked to. The status metrics they showed were duplicated by
  the dashboard and Cost Center, the node-pool internals were unactionable for
  non-technical users, and the configuration knobs already live in the
  Component Catalog. Job cancel/resubmit and per-user spend controls will be
  reconsidered in the experiment, pipeline, and Cost Center surfaces where
  users actually look for them.
- Stale bookmarks to `/compute`, `/compute/cluster`, `/compute/jobs`, and
  `/compute/quotas` now redirect to the dashboard.
- The backend endpoints those pages consumed are removed:
  `/api/v1/infrastructure/compute/{status,metrics}`, `/api/compute/cluster`,
  `/api/compute/jobs*`, `/api/compute/budget`, and `/api/quotas*`. The BAL
  compute adapter (`get_compute_adapter`) is unaffected; it remains the
  abstraction used by pipeline runs, work nodes, and the cost service.

## v2026.6.3

### Naming Profiles

Replaced the original Naming Profiles feature with a parse-only, template-driven design.
See [ADR-058](decisions/ADR-058-naming-profile-parse-only.md).

- Naming Profiles now read information **from** filenames; bioAF never renames files
  based on a profile. The original closed enum of segment field names is gone, replaced
  by an unbounded, template-driven field vocabulary.
- A new wizard lets a team author a profile with three segment shapes: `number`
  (e.g. `SMP0042`), `string` (e.g. `req-bmills` with inner separator opposite the
  delimiter), and `date` (`YYYYMMDD`, `YYYY-MM-DD`, or `YYMMDD`). Non-date segments
  carry a 1-4 letter identifier so order in the filename does not matter.
- Three system segments (Project / Experiment / Sample) are always available regardless
  of which Experiment Template is selected.
- The wizard supports a live test field: paste a real filename and see what the
  in-progress profile would parse out, with unrecognized tokens and warnings.
- Clicking a saved profile opens a detail modal with an example filename, the parser
  test, and an Edit button.
- Experiment Templates and Experiments both gain an optional `naming_profile_id`.
  Experiments inherit the template's default and can override it per-experiment.
- Auto-ingest is temporarily disabled (returns 503 on `POST /api/ingest/simulate`) while
  the follow-up rework picks profile selection at parse time. Flip
  `AUTO_INGEST_DISABLED` in `app/services/auto_ingest_gate.py` to re-enable.

## v2026.6.2

### Reference data

- The URL importer now resumes a dropped download via HTTP
  `Range: bytes=<N>-` instead of starting over from byte 0. When the
  source CDN closes the TCP connection mid-stream (a 10x Genomics
  11.4 GB reference died at 7.1 GB with `peer closed connection without
  sending complete message body`), the importer reissues the GET with a
  Range header pointing at the next byte it needs, the server returns
  `206 Partial Content`, and only the remaining bytes are re-fetched.
  If the server doesn't honor the Range request (returns `200 OK` with
  the full body), the importer falls back to a clean restart so the
  final object is still correct. Up to 3 attempts with 5s / 10s / 20s
  exponential backoff.

- To support resume cleanly across all extract modes
  (`none` / `gzip` / `tar` / `tar.gz`), the source URL is staged to a
  local tempfile during the download phase and only uploaded to GCS
  once the download finishes (and the optional `.md5` verification
  passes). This requires `<size of largest reference>` of free space
  on the backend container's `/tmp`. On a 10 GB import that's a
  one-time scratch cost; the tempfile is deleted as soon as the upload
  completes (or the import fails).

- MD5 verification now happens before any bytes hit GCS. Previously the
  importer uploaded as it streamed and deleted the blob on mismatch;
  now a mismatch means no blob is ever created, so a failed MD5 leaves
  the references bucket clean by construction.

- The URL-import path now finalizes the dataset row after the worker
  finishes. Previously `ReferenceImportProgress` reached `active` but
  `ReferenceDataset.status` stayed at `uploading` forever, so the UI's
  `Importing` badge persisted even on a completed 10.66 GB import. The
  service now writes a `ReferenceDatasetFile` per imported file,
  aggregates `total_size_bytes` / `file_count` / `md5_manifest_json`,
  flips `dataset.status` to `pending_approval` for public datasets or
  `active` for internal (matching the upload-flow rule), and writes an
  `import_completed` audit entry.

- The Type column on the reference detail page now shows a friendly
  label for the common bioinformatics formats and STAR genome-index
  files (`FASTA`, `FASTA index`, `GTF`, `VCF`, `BAM`, `AnnData`, `Loom`,
  `Matrix Market`, `STAR genome`, `STAR SA index`, etc.) instead of an
  em-dash. The new `detect_reference_file_type` is applied on write
  (URL-import finalization persists it) and as a read-side backfill in
  the detail response, so already-imported datasets light up without a
  data migration. Unknown extensions still render as the empty-value
  placeholder; the detector does not invent a category.

- For reference datasets already stuck in `uploading` from before the
  finalize fix landed (or any future case where the backend crashed
  between worker completion and the finalize step), the detail page
  now shows a `Finalize import` button alongside `Cancel import`
  whenever the worker reports `finalizing` or `active` but the dataset
  row is still `uploading`. The button POSTs
  `/api/references/{id}/recover-finalize`, which lists the blobs that
  exist under the dataset's `gcs_prefix`, builds an `ImportResult`
  from what's actually there, and runs the standard `finalize_import`
  -- so a 10 GB stuck row can be recovered without re-downloading
  anything.

## v2026.6.1

### Security

- Bump `dompurify` 3.4.4 to 3.4.7 to patch the XSS-via-selectedcontent-reclone
  advisory (Dependabot alert #54, high).
- Bump `idna` 3.11 to 3.17 to patch an IDNA bypass of the CVE-2024-3651 fix
  (Dependabot alert #52, medium).

## v2026.6.0

### Reference data

- Reference data import-from-URL works end to end. The endpoint previously
  500'd on every call because the backend tried to load a kubeconfig that
  doesn't exist in the VM-resident container, and the importer image and
  service account it referenced had never been built. The import now
  runs as an in-process asyncio background task in the backend: the
  HTTP request returns immediately after creating the dataset and
  progress rows, and the task streams the source URL straight into GCS
  via the existing `UploadService` credentials, optionally verifies an
  upstream `.md5` file, and optionally extracts gzip / tar / tar.gz
  archives. Progress is written directly to `ReferenceImportProgress`
  per chunk so the Import Status modal updates in real time. There is
  no GKE Job, no importer image, no internal callback token, no
  separate KSA -- the worker code is the running backend code, so a
  dev / branch build never desynchronizes from the worker the way a
  Pod-based design would.

- A backend restart mid-import leaves the row in whichever state was last
  reported (typically `downloading`) and does not resume; the user
  cancels the stuck import (existing `Cancel` button on the Import
  Status modal) and retries.

- The reference detail page now shows in-flight import progress and a
  `Cancel import` button when the dataset is in the `uploading` state,
  and an error banner with a `Delete` button when the dataset is in
  the `failed` state. Previously these controls were only on the
  import wizard, so navigating away during a long download left no way
  to see progress, cancel, or clean up a stuck or failed dataset.

- Force GCS uploads to a resumable / chunked upload with an 8 MiB
  chunk size on every blob the importer writes. Without this the
  google-cloud-storage SDK tries to buffer the entire body in memory,
  which back-pressures the URL stream and silently stalls multi-GB
  imports well before they finish (e.g., a 10 GB 10x Genomics
  reference would freeze around 56 MB). With chunked uploads, each
  8 MiB block is PUT to GCS as soon as it has been read.

- Merge "Add Reference" and "Import from URL" into a single
  "Add Reference Data" button. The unified page has an Upload / URL
  Import toggle at the top and renders the matching form. The
  "Upload new version" button on the reference detail page now
  deep-links into the same page with the Upload side preselected.

- The version field on both forms is now optional and auto-populated.
  When the user enters or selects a name + category, the page fetches
  the existing versions for that name and prefills the field with the
  next `v<N>` (or `v1` if this is the first version). Users keeping
  their own naming convention (e.g., `GRCh38.p14`) can still type any
  value to override.

- The URL Import form now auto-selects the extract mode from the
  source URL's file extension: `.tar.gz` / `.tgz` -> `tar.gz`,
  `.tar` -> `tar`, `.gz` -> `gzip`, anything else -> `none`. As soon
  as the user changes the extract dropdown themselves, later URL edits
  no longer overwrite their selection.

## v2026.5.27

### Setup wizard

- Add a "Select Components" step at the end of the wizard. After the user
  picks Kubernetes and kicks off the stack deploy, the wizard surfaces the
  same component grid as the post-install Infrastructure > Components page
  with `nextflow` and `jupyterhub` pre-checked. Selections are queued
  immediately, and the new "Deploying" step shows live per-component status
  (queued, building, ready, failed) so the user can leave with confidence
  that the system will be ready for their first experiment and pipeline
  without having to re-open the Infrastructure menu.
- Add a "Back" link on every step from Create Admin through Select Stack,
  preserving previously entered field values. Back is hidden once the
  Terraform deploy has been triggered.
- Re-submitting an already-completed wizard step no longer fails. If the
  user goes back to a finished step and clicks Continue with unchanged
  values, the wizard advances without re-calling the backend. If the user
  edits a value, the wizard shows the diff and asks to confirm the
  overwrite. A "Forward" link appears alongside Back on completed steps so
  the user can go back to verify a value (e.g. "which email did I use for
  admin?") and forward again without re-submitting. The Create Admin
  backend endpoint is now idempotent: a second call with the same setup
  token updates the existing admin instead of returning 409.
- Widen the setup card so the 10-step indicator has breathing room.

### Component lifecycle

- Add a `queued_for_infra` component state and a `process_queued_components`
  orchestrator that drains queued components as readiness flips: cluster-only
  components (`nextflow`, `snakemake`, `qc_dashboard`, `meilisearch`) move to
  enabled when the cluster is up; image-bound components (`jupyterhub`,
  `rstudio`, `cellxgene`) kick off Cloud Build as soon as storage is up and
  flip to enabled once both image and cluster are ready. The orchestrator
  runs from `stack/deploy-background`, the storage- and compute-ready hooks
  in `deploy_stack`, and each image poll's SUCCESS branch.
- Add `POST /api/components/select-batch` for the wizard to queue components
  transactionally, validated against the canonical Kubernetes component list.
- Surface `has_in_flight_components` on `GET /api/bootstrap/status` so
  returning users mid-deploy can be routed back to the components view.
- `POST /api/v1/infrastructure/stack/deploy-background` now records
  `compute_stack` in `platform_config` up front so the wizard's component
  fetch resolves to the right list before the compute module finishes.

### Fixes

- Close a drift between `KUBERNETES_COMPONENTS` and `COMPONENT_CATALOG`: the
  toggle endpoint accepted `jupyterhub` while the catalog only had
  `jupyter_k8s`, so the status flip in the image services was a silent no-op
  against a non-existent catalog entry.
- Render `queued_for_infra` as a "Queued" badge on the existing components
  page instead of falling through to the default "Disabled" treatment.
- Drop the dollar-estimate badges from per-component cards in the wizard to
  match the post-install page.

## v2026.5.26

### Notebook sessions

- Add larger notebook resource profiles for real single-cell work. The ladder
  is now Small (2 CPU / 8 GB), Medium (4 CPU / 16 GB), Large (8 CPU / 32 GB),
  X Large (16 CPU / 64 GB), and XX Large (16 CPU / 128 GB). The previous
  options topped out at 8 CPU / 16 GB, too small for Seurat/scanpy integration.
- Redesign the launch modal's resource profile picker as a full-width list of
  rows (tier name, CPU/RAM, and a short use-case description), matching the
  work-node machine-type picker so the full range of tiers is legible. The
  modal now pins the launch buttons so they stay visible while the body
  scrolls.
- Gate resource profiles to the interactive GKE pool's node size. Notebook pods
  run with requests == limits on the interactive pool, so a tier only schedules
  if it is strictly smaller than a single node. Tiers that exceed the configured
  `k8s_interactive_machine_type` are now shown but disabled; clicking one
  explains that an admin must increase the interactive pool. Prevents notebooks
  silently sitting in Pending when a too-large profile is chosen.

### Infrastructure

- Surface the notebook tiers supported by the interactive pool's configured
  machine type in Infrastructure > Components. The Interactive Pool status card
  now shows "Machine Type: \<Tier\> (\<machine_type\>)" (e.g. "Medium
  (n2-standard-8)") and a "Supports ... notebooks" line; the Cluster
  Configuration form refreshes the supports line live as the dropdown changes.
- Extend the interactive pool dropdown with `n2-standard-32` (compute-intensive
  analysis) and `n2-highmem-32` (extreme memory workloads). The previous top
  option (`n2-highmem-16`) only unlocked through Large, leaving X Large and
  XX Large unreachable from the UI; admins can now pick a machine that
  unlocks every notebook tier.
- Remove the disconnected `interactive_pool_machine_type` /
  `interactive_pool_max_nodes` fields from the K8s Interactive Node Pool
  component. They saved to component state but were never applied; the pool is
  sized by the Terraform-wired cluster config (`k8s_interactive_machine_type` /
  `k8s_interactive_max_nodes`) in Infrastructure > Components, now the single
  source of truth.

### Notebook environments

- Expand the default notebook image with the packages a bioinformatician
  actually needs: single-cell (scanpy, anndata, scvi-tools, harmonypy,
  scrublet, doubletdetection, celltypist, decoupler, muon, scVelo; Seurat with
  hdf5r, harmony, and presto), bulk RNA-seq differential expression (DESeq2,
  edgeR, limma), and enrichment/annotation (clusterProfiler, fgsea, SingleR,
  org.Hs.eg.db, org.Mm.eg.db).

### Work nodes

- Fix work nodes getting stuck in "Starting" even after the VM is up and SSH
  reachable. The launch request now commits the "starting" status before
  creating the VM and no longer rewrites status/access_url afterward, so the
  background readiness poll's "running" transition (and SSH URL) is no longer
  clobbered by the launch request's own commit.
- Make the work-node boot disk size and type configurable from Workbench
  Settings (default 100 GB pd-ssd, down from a hardcoded 200 GB). pd-ssd and
  pd-balanced both count toward the regional SSD_TOTAL_GB quota, so a large
  default could exhaust it and fail launches with a quota error; operators can
  now shrink the disk or switch to pd-standard (which uses the separate HDD
  quota) per their workload.
- Make multi-zone failover actually work when a zone is out of capacity.
  `instances.insert` is asynchronous, so a `ZONE_RESOURCE_POOL_EXHAUSTED`
  stockout only surfaced when the operation was resolved, which the launcher
  never did. As a result the failover loop broke on the first zone, the launch
  silently never created a VM, and it failed five minutes later with a
  misleading "no external IP" error. The launcher now resolves the insert
  operation so a stockout advances to the next zone in the region.

### Fixes

- Stop the default notebook image from silently shipping without R packages
  such as Seurat. `install.packages()` returns success even when a package
  fails to build, so the image now installs R packages as precompiled binaries
  from the Posit Public Package Manager and fails the build if any expected
  package is missing.

## v2026.5.25

### Notebooks

- Rebuild the Default Notebook environment as a Conda specification. It had
  drifted to a hand-maintained Dockerfile whose image build failed, and it now
  ships the full single-cell stack again: scanpy, anndata, scvi-tools,
  harmonypy, scrublet, and gseapy on the Python side, plus Seurat and the core
  Bioconductor packages (SingleCellExperiment, scran, DESeq2, edgeR, limma,
  clusterProfiler, fgsea, ComplexHeatmap, and friends) reachable from R through
  the Jupyter R kernel.

## v2026.5.24

### Networking Settings

- Settings -> Networking now matches the real bioAF install topology (single
  VM behind nginx) instead of the GKE Ingress topology assumed by the
  initial release. The certificate status pill is now driven by the actual
  on-disk nginx certificate: if `/etc/nginx/certs/tls.crt` is currently
  valid and its Common Name or a SubjectAltName matches the configured
  FQDN, the card shows the cert as Active; otherwise it shows as Not
  requested so the operator knows to install one.
- Clicking **Request certificate** now returns an operator-facing
  instruction with the exact certbot command to run on the host, rather
  than recording a fake "Provisioning" state that never advances.
  Automated, in-UI Let's Encrypt issuance is tracked as a follow-up that
  requires a host-side cert agent on the VM.
- The unused GKE-targeted helm scaffolding (`ManagedCertificate` template
  and the `networking.gke.io` RBAC role) has been removed from the chart.
- Internal: removes `KubernetesNetworkingApplier` and replaces it with
  `VmNginxApplier`. `get_networking_applier()` no longer branches on
  `BIOAF_COMPUTE_MODE`. The applier Protocol is unchanged, so the Settings
  page, the reachability test, and the HTTPS-enforcement flag are
  unaffected.
- The cert status pill now reflects the actual on-disk certificate at every
  page load: a stale "Provisioning" cached from an earlier click on a
  previous build is cleared and replaced with the truthful state as soon as
  the page loads.
- When the cert request returns the manual-setup instructions (current
  default on VM installs until the host-side cert agent ships), the UI
  shows a structured "Manual setup required" callout with the certbot
  command in a code block and a Copy button, instead of the previous
  single-line red `ApiError: ...` toast.
- New installs are now ready for Let's Encrypt out of the box:
  - `install-gcp.sh` installs `certbot` and creates `/var/www/letsencrypt`
    on first boot of the VM, alongside the existing Docker install.
  - `./install.sh prepare-letsencrypt` installs certbot + creates the
    webroot on any Debian/Ubuntu host. `./install.sh` (full install) also
    runs this best-effort.
  - `docker/nginx.conf` now serves `/.well-known/acme-challenge/` over HTTP
    on port 80 from a new bind mount, so `certbot certonly --webroot -w
    /var/www/letsencrypt` works without stopping nginx.
  - The applier's instruction text has been tightened to include the
    explicit `cp /etc/letsencrypt/live/<fqdn>/{fullchain,privkey}.pem
    docker/certs/...` commands and the `./bioaf restart` that picks up
    the new cert.
- The backend container now mounts `docker/certs` so the networking
  applier can read the live nginx cert. Without this mount the cert pill
  stayed at "Not requested" even after the operator installed a valid
  Let's Encrypt cert.
- `https_enforced` is now driven by the install topology rather than a
  DB flag the operator must toggle: VmNginxApplier reports it as always
  true because nginx.conf unconditionally redirects HTTP to HTTPS. The
  cert card now shows the green "HTTPS is enforced" indicator immediately
  and hides the "Apply HTTPS and restart" button (which had no effect on
  VM installs anyway).
- The "Request certificate" button is hidden once the cert pill shows
  active. Operators no longer see a stale CTA prompting them to repeat
  work that has already been done.

## v2026.5.23

### Networking Settings

- New **Settings -> Networking** page lets an operator configure the public
  hostname and domain, verify the FQDN routes to this instance, request a
  Google-managed TLS certificate, and enforce HTTPS with a controlled
  service restart. The page is a three-card wizard: each card unlocks the
  next based on the underlying state, so the operator cannot request a cert
  before the FQDN is verified or enforce HTTPS before the cert is active.
- The reachability check is a loopback through public DNS: bioAF writes a
  one-time nonce, then calls itself at `https://<fqdn>/api/v1/settings/networking/self-check`
  and confirms the response carries the nonce. This proves not just that DNS
  resolves, but that requests actually return to this bioAF instance.
- HTTPS is attempted first (certificate verification disabled, since the
  cert may not yet cover this hostname), with an HTTP fallback for the
  pre-enforcement state.
- Reachability failures are translated into human-readable detail with a
  next action: DNS resolution failed ("wait for negative DNS cache to
  expire, then retry"), connection refused ("check the Ingress is routing
  port 443 for this FQDN"), TLS handshake failed ("the cert may not yet
  cover this hostname"), and so on. The raw libc error strings no longer
  leak into the UI.
- Clicking **Refresh status** on the certificate card now shows a
  "Refreshing..." state on the link, briefly outlines the status pill on
  completion, and stamps a "Last checked HH:MM:SS" line so the operator can
  see the poll happened even when the status itself did not change.
- bioAF still does not manage DNS or external OAuth/SSO callback URLs.
  Those remain the operator's responsibility and are flagged on the page.
- All mutating actions are gated by the `infrastructure:edit` permission
  and recorded in the audit log.

## v2026.5.22

### Fixes

- Fix every nf-core pipeline run failing at the MULTIQC step with
  `java.lang.StackOverflowError`. The generated `nextflow.config` set the
  MULTIQC `ext.args` to a self-referential closure
  (`{ (task.ext.args ?: '') + ' --export' }`) that recursed forever when
  Nextflow resolved it. `ext.args` is now a plain `--export` override.

## v2026.5.21

### Password reset

- Password reset emails now contain a working reset link (valid for 60 minutes)
  alongside the 6-digit code. Following the link opens a page where you enter the
  code, set and confirm a new password, and are redirected to sign in. This
  replaces the old email that contained only a bare code with nowhere to use it.
- Add a "Forgot password?" link on the sign-in page so users can request a reset
  themselves.
- In Settings > Users, an admin can always set a user's password manually ("Set
  Password Manually"), even when SMTP is configured; previously the manual option
  was hidden whenever email reset was available.
- The reset link in the email is now an absolute URL (built from the request host
  and scheme) instead of a host-less path that browsers rejected.
- Sending a reset email now reports a real failure (e.g. bad SMTP credentials)
  instead of falsely claiming success, and the confirm button shows a working
  state while it sends so it is clear the click registered.

### Profile

- Clicking your name in the top-right header now opens a single Profile page with
  tabs for Account, Session Credentials, Git SSH Key, and Notifications.
- The Account tab shows your email and role and lets you set your display name and
  change your password. Your name (falling back to your email) is what now shows
  in the header.
- Remove the role badge from the header, the username/role block from the bottom
  of the sidebar, and the Profile menu from the sidebar navigation.

### Fixes

- Allow deleting a deactivated user who never logged in even if an admin had sent
  them a password reset. The pending reset code no longer blocks the deletion, and
  the admin's action remains recorded in the audit log.
- Outbound email no longer stops working after a rebuild or restart. The saved SMTP
  password was reloaded from the database without being decrypted, so the server
  login used the encrypted value and failed until the password was re-entered and
  saved again. It is now decrypted on load.

### Security

- The SMTP password is scrubbed from application logs. A redaction filter on every
  log handler replaces the live value with `***`, so the credential cannot leak
  through a log line, exception, or traceback.

## v2026.5.20

### Dashboard

- Replace the fixed infrastructure widget grid with a customizable, role-aware
  dashboard. A gear menu lets each user choose which widgets to show; the choice
  persists per user, and a sensible default set ships per role. Widgets are only
  offered and rendered when you have permission to view the data they show.
- Add dashboard widgets: experiments status, active pipeline runs, failed runs
  (with a 24h/12h/1h window), runs awaiting review, queue depth, my custom
  pipelines, my active sessions (notebooks and work nodes), recent plots, recent
  literature, my reading list, team output this week, cost vs budget, cost trend,
  infrastructure health, backup status, auto-ingest activity, pending invites,
  and the activity feed.

## v2026.5.19

### Fixes

- Fix BigQuery billing export verification failing with a 403
  (`bigquery.tables.list denied`). The dataset read grant was landing on the
  project's default compute service account instead of the service account the
  backend actually queries as (`bioaf-app`), so verification and cost sync could
  never read the dataset. The grant now targets the runtime service account, and
  clicking **Verify** on an already-affected install self-heals the permissions
  by re-applying the billing export module, then completes: no manual `gcloud` or
  `bq` commands required.

## v2026.5.18

### Search

- Add a full search results page at `/search`, reached by pressing Enter in the
  global search bar instead of picking one of the dropdown hits. It searches
  experiments, samples, pipeline runs, files, projects, pipeline definitions,
  and literature papers, matching both names and content (descriptions,
  abstracts, and the like), and hides any type you do not have permission to
  view.
- Results are a single relevance-ranked list of cards, each tagged with its
  type and a context line that tells similar-looking results apart (for
  example, four files with the same name by the run that generated them and
  their sample). A type filter narrows results to one kind and shows a per-type
  count.
- Experiment result cards show the parent project, the number of samples,
  files, and pipeline runs, and the last activity date.
- Clicking a result takes you straight to the item.

## v2026.5.17

### Results and QC

- Surface AI Review on the QC report. A minimizable "AI Reviews" panel at the
  top of the report lists the run's review cards (click to open the full
  review), and a "Run AI Review" / "Re-run AI Review" button starts a review.
  This appears on the standalone QC dashboard, the Pipeline Run Results tab,
  and the Experiment Results tab.
- Anyone who can view experiments or pipelines can now read AI reviews on the
  QC report; running or dismissing a review still requires the AI Review
  permission.
- The Experiment detail Results tab now opens the same full QC report used
  elsewhere in a modal (click outside to close), instead of a reduced
  look-alike.

### Fixes

- Fix broken plot previews on the Experiment Results tab (PDF plots now show
  their thumbnail and open the plot viewer, matching the Plot Archive).
- Show full context on the Experiment Results tab QC cards (pipeline, project,
  experiment, and samples) instead of just the run number.
- Show the AI Review trigger to computational-biology users, not only admins.

### Permissions

- The Results menu and the Results tabs are hidden from users who cannot view
  experiments or pipelines.

## v2026.5.16

### Notifications

- In-app notifications are now clickable and open the item they refer to: a
  completed run's QC dashboard, an experiment, an uploaded file, and so on.
  Clicking still marks the notification read. Existing notifications are
  backfilled so they link too.

### Navigation

- Surfaced Experiments as a top-level sidebar section (it was nested under
  "Projects"); the sub-menu is unchanged.
- Renamed the two identical "Environments" sidebar entries to "Pipeline
  Environments" and "Compute Environments".
- Reordered the sidebar to lead with the science: Dashboard, Experiments,
  Pipelines, Results, Workbench, Data & Files, Profile, Infrastructure, Settings.

### Global shell

- Added a global search box to the header to jump to any experiment, sample,
  pipeline run, or file by name (results appear after you pause typing).
- Added a "+ New" quick-create menu (New Project, New Experiment, New Sample).

### Results

- The QC dashboard detail is now labeled by Project / Experiment / Pipeline and
  links back to its pipeline run, instead of the unplaceable "QC Dashboard -
  Run #N".

## v2026.5.15

### Settings

- Notebook settings and work-node settings are now a single **Workbench
  Settings** page (Settings > Workbench Settings) with separate Work Nodes and
  Notebooks cards.
- Slack configuration now lives only in Settings > Integrations (Slack tab). The
  duplicate standalone Slack settings page was removed and its URL redirects
  there.

### Fixes

- The Job Browser and Quotas buttons on Infrastructure > Compute work again.
  They were previously caught by redirects that bounced back to the cluster page.

### Removed

- Cleaned up duplicate and unreachable pages left over from earlier navigation
  changes (old Results, Data, Pipelines, References, and Compute landing pages,
  the standalone component catalog, the ingest dashboard, and the global pipeline
  triggers page). Their old URLs redirect to the current locations. Automated
  pipeline runs are configured per experiment, from the experiment's Pipeline
  Runs tab.

## v2026.5.14

### Infrastructure

- "Check for Infrastructure Updates" no longer fails its background apply with
  a GCP 400 ("Must specify a field to update"). GKE node pools stop churning on
  a no-op `node_locations` diff, and the additive-only apply now skips a benign
  no-op update instead of aborting the whole batch.
- Recent Operations spells out why a plan run ended instead of showing a bare
  "cancelled": "No changes" when nothing was planned, "Not applied" when changes
  were found but not applied by that run, and the abandon reason in a tooltip for
  user-cancelled runs.

## v2026.5.13

### Literature

- New Literature Library under Data & Files. Upload PDFs and bioAF
  extracts the title, authors, DOI, and abstract automatically. Add
  threaded comments per paper, track your own reading status, and
  associate papers with experiments or projects so they show up where
  your work lives.
- Search across PubMed, bioRxiv, Europe PMC, and Semantic Scholar from
  one place. Results stay outside the Library until you tick the ones
  you want and click "Add to Library". Inline HTML tags and entities in
  titles and abstracts are cleaned on ingestion so display is plain
  text. Dedup by DOI prevents the same paper appearing twice.
- New "Lit Review" job on experiments: the platform asks your active
  LLM to generate adjacent search queries, pulls candidate papers from
  every enabled source, then scores them for relevance. Top picks are
  added straight to the Library, associated with the source experiment,
  and tagged with an "AI Lit Review Bot" note on the paper detail
  explaining why each was recommended.
- Library filters now use independent toggles for Active / Dismissed /
  Read / Unread / Reading plus project and experiment association
  pickers. Each row shows multiple status flags at once (e.g. dismissed
  and read together). Multi-select + bulk Associate works inline.
- Agent Review now reads from the library too. Abstracts and team
  comments on papers associated with the experiment can be bundled
  into the Agent Review prompt; admins control which inputs are on
  per org, project, or experiment, and can set a token budget so
  large libraries do not blow up the review.
- Export citations as BibTeX or RIS from any paper detail page; bulk
  export covers all papers in the current filter or scope.
- Read uploaded PDFs in the app: the paper detail page now has a
  paginated reader that shows one page at a time with Prev / Next and a
  page counter, plus a Download link. Your reading status advances as you
  read: reaching the second page marks a paper Reading and reaching the
  last page marks it Read (it only ever moves forward, so it never undoes
  a status you set by hand).
- Delete a paper from the Library (admin / comp_bio): this removes the
  uploaded PDF and any stored files from cloud storage to free space, and
  dismisses the paper so it leaves your active Library and future AI
  Literature Review. Its abstract, metadata, comments, and history are
  kept, and an admin can reverse the dismissal later (the PDF would need
  to be uploaded again).
- Automated AI Literature Review (Settings > Integrations > LLMs): turn on
  a daily, weekly, or monthly cadence and bioAF runs Lit Review on its own
  for experiments that gained new samples or pipeline runs since their last
  automated review. You pick the first-run date and time; it then repeats on
  the chosen cadence. New papers land in the Library with the AI note;
  dismissed papers and papers below the relevance lower bound are never
  re-recommended. A configurable cap limits how many experiments run per
  cadence (oldest activity first; the rest roll to the next run). When an
  automated run adds papers, the affected users get an in-app notification.
- Bulk Dismiss in the Library: when papers are selected, a Dismiss button
  appears next to Associate and Clear so you can dismiss several at once when
  you immediately recognize they won't fit. Dismissal is org-wide and
  reversible by an admin, same as dismissing a single paper.
- Associate a paper from its detail page: the Associations card now has an
  Associate button (the same Project / Experiment picker as the Library), so
  you can link a paper to your work while you're reading it instead of going
  back to the Library list.
- Agent Review of a pipeline run now checks the results against the
  experiment's associated literature and flags results that are unexpected
  or contradict prior work (a default-on, admin-toggleable review topic).
  When a paper's full text is included in the review, the assistant cites
  the page; uploaded PDFs are now stored with page markers so those
  citations resolve.

### Fixes

- Agent Review's "Pipeline-specific biological signals" section is now selected
  by default (all of its assay-specific topics), matching the other sections.
- Agent Review no longer gets stuck after a failed run. If a review job hit an
  unexpected error while preparing its input or calling the LLM (or its worker
  stopped), it could be left in an "in progress" state, after which every new
  review attempt failed silently with "Request failed". Now: unexpected errors
  mark the job failed with the real reason shown on the card; a genuinely
  in-progress review shows a clear "already in progress" message instead of the
  generic one; and a review left in-progress past the longest plausible run is
  automatically reaped on the next attempt, so a stranded review can no longer
  block all future reviews until a restart.
- Infrastructure > Components now lists every provisioned GCS bucket. The
  references and literature buckets were missing because the metrics read
  path queried an incomplete set of bucket-name keys; it now derives the
  keys from the same source the listing iterates, so the view reflects
  what is actually deployed.
- The paper-detail PDF reader no longer compresses pages vertically. The
  page now keeps its true aspect ratio instead of being squished to fit
  the viewer height.
- Uploading a paper that already exists in the Library (for example one
  added earlier from a search or AI recommendation) now attaches the PDF
  to that existing entry instead of just taking you to it with no file
  saved. The matched paper is also pulled into the Library if it was not
  already there.

## v2026.5.12

### Internal

- Rewrite the stack-deploy error sanitizer to return the matched allowlist constant rather than the input string. Output to the client is identical, but CodeQL now recognises the result as untainted.

## v2026.5.11

### Internal

- Add an explicit `os.path.basename` barrier after the restore-filename regex check so CodeQL's path-injection taint tracker recognises the sanitization. No behavior change for any user.

## v2026.5.10

### Security

- Harden the database restore endpoint against path-traversal: the restore request now rejects any filename that isn't a real `pgdump-<timestamp>.dump` produced by the backup service. No effect on legitimate restores from the UI.

## v2026.5.9

### Internal

- Drop the unused `scripts/seed_poc_data.py` POC seed script. The `./bioaf seed` command still works with any script you place in `scripts/`.

## v2026.5.8

### Stability and security

- Continued tightening of server-side logging so resource identifiers
  involved in launching and tearing down compute sessions are no longer
  flagged as carrying sensitive data.

## v2026.5.7

### Stability and security

- Additional cleanup of server-side log messages so sensitive-looking
  identifiers and details no longer appear in plain text in operator logs.

## v2026.5.6

### Stability and security

- Tightened how the application reports internal errors so failures show a
  short, friendly message in the UI while the full diagnostic detail stays in
  the server logs for operators.

## v2026.5.5

### Security

- Restrict GitHub Actions workflows to read-only `contents` permission so the
  default `GITHUB_TOKEN` cannot mutate the repository, addressing 10 code
  scanning alerts in `ci.yml`, `build.yml`, and `changelog-check.yml`.

## v2026.5.4

### Security

- Replace the unmaintained `python-jose` JWT library with `PyJWT`. The previous
  dependency pulled in `python-ecdsa`, which is subject to the Minerva timing
  attack on P-256 and has no upstream fix planned. The platform only signs
  HS256 tokens, so the swap is behaviour-preserving.
- Pin nested `postcss` to `>=8.5.10` via an npm `overrides` entry to clear the
  XSS-via-unescaped-`</style>` advisory that surfaced through Next.js's bundled
  copy of `postcss@8.4.31`.

## v2026.5.3

### Stability

- Upgrade the frontend framework to the latest 15.x maintenance line.
  Pulls in upstream security patches for cache and rendering edge cases.
  No user-visible behavior change: existing logins, notebooks, pipeline
  runs, and bookmarked URLs continue to work without re-login or
  reinstall.

## v2026.5.2

### Stability

- Refresh backend and frontend dependencies to pull in a batch of
  upstream patches addressing minor platform stability and
  dependency-hygiene bugs. No user-visible behavior change; existing
  data, logins, and active notebook or pipeline sessions are
  unaffected by the upgrade.

## v2026.5.1

### Agent Review (LLM integration v1)

- New **Settings > Integrations > LLMs** page lets an admin configure
  one or more hosted LLM providers (OpenAI, Anthropic Claude, Google
  Gemini) and pick exactly one active at a time. API keys are
  encrypted at rest; activating a hosted provider triggers a
  data-egress warning. A self-hosted Gemma 4 backend is in the
  product but its per-request inference path is not yet wired up, so
  the option is hidden from the UI in this release.
- New **AI Review** tab on Pipeline Run and Experiment detail pages.
  Two buttons on Pipeline Run, "Review this pipeline run" and "Review
  across experiment," dispatch async jobs that return severity-coded
  advisory cards (red, orange, green) with a free-text body and a
  prompt-details disclosure. Each tab shows only its own scope:
  pipeline_run reviews live on the run page, experiment reviews live
  on the experiment page. Cards filter by active, dismissed, stale,
  or failed and are dismissable org-wide.
- **Section-builder prompt assembly.** The review modal lets the user
  toggle individual prompt sections (QC, metadata, biological signal,
  cross-sample, interpretation) before running. Defaults are scoped
  per review type. The assembled prompt is previewable via "Display
  prompt," editable for a single one-off run, or saveable as a named
  custom prompt for reuse.
- **Experiment review payload.** Cross-experiment reviews now ship
  the experiment metadata (name, design type, hypothesis, protocol
  version, design variables) plus every sample on the experiment in
  a wide samples table, along with the per-run artifacts. The
  selector includes a master "select all" checkbox for both the runs
  and the optional HTML-report attachments.
- **Audit and egress.** LLM output is advisory only: it never enters
  provenance or any submission artifact. Every invocation writes an
  audit row with provider, model, the last 5 characters of the API
  key, and the GCS paths of the transmitted `.md` artifact, so the
  org can answer "did we ever send sample X to an LLM" with a single
  SQL query.
- **RBAC.** New permissions `llm_integration:configure` (admin) and
  `llm_integration:use` (admin and comp_bio at bootstrap); migration
  081 backfills both into every existing org's system roles.

See ADRs [052](decisions/ADR-052-llm-integration-trust-boundary.md),
[053](decisions/ADR-053-llm-provider-abstraction.md),
[054](decisions/ADR-054-gemma-per-request-inference.md), and
[055](decisions/ADR-055-agent-review-advisory-entity.md).

## v2026.5.0

### Release process

- Switched from SemVer to CalVer (`YYYY.MM.N`, e.g. `2026.5.0` for the first
  release in May 2026, `2026.5.1` for the second). The release workflow now
  computes the next version on each push to `main`, assembles a changelog
  section from per-PR snippets under `changes/unreleased/`, commits the
  version bump back to `main`, tags the release, and publishes Docker
  images. No more manual version edits.
- The Update button continues to work across the cutover with no user
  action required. CalVer tags are three numeric segments, so the deployed
  install validator on every existing client (including pre-cutover `0.x`
  releases) accepts them; tuple comparison correctly recognizes them as
  newer than any prior SemVer.

## v0.15.1

Adds reviewer ergonomics to the Pipeline Run page and surfaces more
context throughout the Results area, plus a fix for empty QC dashboard
plot grids after the nf-core pipeline upgrade.

### Pipeline runs

- New **Results** tab on the Pipeline Run detail page, placed before the
  Review tab. Embeds the QC dashboard for the run (interactive metrics,
  charts, and static plots) and the Plot Archive entries scoped to that
  run, with deep-links to the full Results > QC Dashboards and Results
  > Plot Archive pages. Reviewers can now see results and submit a
  review without leaving the page or refreshing.

### QC Dashboards

- The QC Dashboards list view now shows project, experiment, sample
  external IDs, and pipeline name on each row instead of just
  `Run #N`. Context is batch-loaded so adding the fields does not
  multiply round-trips.
- Fixed the empty plot grid that appeared after upgrading
  nf-core/scrnaseq (and other nf-core pipelines on newer MultiQC):
  MultiQC 1.20+ stopped writing `multiqc_plots/png/` by default, so the
  collector had nothing to ingest. The generated `nextflow.config` now
  scopes `ext.args = ' --export'` to the `MULTIQC` process, so future
  runs produce the static PNGs again. Already-completed runs need to be
  re-launched (Reproduce) to regain their plots.

### Docs

- Surfaced the Integration API documentation on the main `README.md`
  (new "Integration API" feature bullet plus a dedicated subsection in
  Documentation linking to the contracts and webhooks references).

## v0.15.0

Hardens the LIMS integration API introduced in v0.14.0 and lands a set of
related identifier, audit, and admin-UX fixes from real-world use.
Published API contracts ship in `docs/api/`.

### Breaking changes (Integration API)

These affect external systems calling `/api/v1/integrations/*` and shipped
before any consumer hit production, but the contract has changed:

- `POST /projects`, `/experiments`, `/samples` now **require** a non-empty
  `external_id`. Missing returns `422`.
- Duplicate `external_id` is **rejected with `409 external_id_already_exists`**
  (previously upserted with `200`). Use `PATCH` to update existing rows;
  use `Idempotency-Key` for safe retries. Duplicate scope: per-org for
  projects/experiments, per-experiment for samples.
- `POST /projects` no longer accepts a `code` field. `code` is now
  server-generated.
- The samples integration field is renamed `sample_id_external` -> `external_id`
  in request bodies, response bodies, the list query parameter, and the
  `sample.created`/`sample.updated` event payloads.

### Identifiers

- Every project, experiment, and sample now carries an internal `uuid`
  (NOT NULL, server-default `gen_random_uuid()`). Internal use only; not
  exposed via API or UI.
- Project and experiment `code` is now a per-org odometer:
  `{4-char org prefix}p-{NNNN}` for projects, `{4-char org prefix}e-{NNNN}`
  for experiments (e.g. `bioap-0008`, `bioae-0025`). Counter lives in the
  new `org_code_counters` table and never decrements on delete. Existing
  codes are left in place; the new scheme applies only to new rows.
- Lists and detail pages show `external_id || code` as the ID. Detail
  pages display both. Sample tables fall back to `#{id}` since samples
  do not have a `code` today.

### Database

- Migration 080 is additive only:
  - `uuid` on `projects`, `experiments`, `samples`.
  - `samples.external_id` (new column; data copied from the now-dead
    `samples.sample_id_external`, which stays in place pending a future
    drop). The SQLAlchemy attribute `Sample.sample_id_unique` is renamed
    `Sample.external_id` and propagated across services, CSV mapping,
    raw SQL, and tests.
  - `org_code_counters` (org_id, kind, next_value).
- No drops, no renames. The old column is recorded in the local-only
  `documentation/dead_columns.md`.

### Admin UX (Settings > Users & Accounts)

- Service accounts no longer appear in the Users tab (`/api/users` now
  filters `is_service_account=False`).
- "Users who have never logged in" panel: excludes service accounts,
  applies a 2-day grace window after invite, and returns / renders
  `role_name` instead of leaving an empty `()` placeholder.
- Service Accounts and Webhooks tabs use centered detail and edit modals
  (no more right-hand drawer); both gain Edit modals matching the Users
  tab pattern. Service Account edit lets you change display name and
  role; Webhook edit covers name, URL, events, and is_active.
- Mint API Key dialog no longer asks for per-key scopes. A key inherits
  its scopes from the service account's role; the dialog shows the role
  and permission count as context.
- "Create custom role" shortcut on Create / Edit Service Account opens
  the same Create Role modal used in Settings > Roles & Permissions and
  auto-selects the freshly created role on save (modal extracted to
  `frontend/src/components/settings/RoleEditorModal.tsx` for reuse).
- API Activity tab Key column shows `{service account name} / {key name}`
  instead of the raw `api_key_id`. The admin endpoint joins `ApiKey` and
  the SA `User` row and returns both labels per audit row.

### Docs

- `docs/api/` now publishes the human-readable contracts for the
  integration API: overview, auth, conventions, per-resource endpoints
  (projects, experiments, samples, files), and webhooks (event catalog,
  signature, retry/dead-letter, replay). The live OpenAPI document at
  `/api/v1/integrations/openapi.json` remains the authoritative schema.

## v0.14.0

First public LIMS integration surface. Introduces a versioned key-authenticated
API at `/api/v1/integrations/*` plus signed outbound webhooks, so external
LIMS systems (Benchling, LabKey, in-house tooling) can read and write
projects, experiments, samples, and file metadata without manual re-keying.
See ADR-048, ADR-049, ADR-050, ADR-051 and addenda to ADR-009 / ADR-032.

### Changes

- **New public sub-app at `/api/v1/integrations/*`.** Mounted as a
  separate FastAPI app with its own OpenAPI document served in
  production. The main app's `/docs` and `/openapi.json` remain gated.
  Resources covered in v1: projects (create/upsert/list/get/patch),
  experiments (same shape, no status writes), samples (no QC writes),
  files (read-only metadata, `gcs_uri` excluded). All endpoints honor
  `Idempotency-Key` retries and upsert by `external_id` on create.
- **Service accounts and API keys.** Org-scoped service accounts are
  `User` rows with `is_service_account=true` and a synthetic
  non-routable email. Keys format `biokey_<prefix>.<secret>`, bcrypt
  hashed, with per-key scope envelopes intersected with the SA role.
  Service accounts cannot log in interactively.
- **Outbound webhooks.** Per-org subscriptions with HMAC-signed
  payloads (`X-bioAF-Signature: t=...,v1=sha256(t.body)`). Background
  worker delivers with `FOR UPDATE SKIP LOCKED`, exponential backoff
  (1m, 5m, 30m, 2h, 12h), and `dead_letter` after five failures.
  Public event vocabulary: `experiment.*`, `sample.*`, `file.*`.
- **Audit-log actor tuple.** New nullable `audit_log.api_key_id`
  column. API-key-authenticated routes write rows with both
  `user_id` (SA) and `api_key_id` so revocation decisions are
  unambiguous in incidents.
- **Admin UI: Users and Accounts.** Settings > Users renamed to
  Settings > Users and Accounts with four tabs: Users (unchanged),
  Service Accounts, Webhooks, API Activity. Key minting and webhook
  creation both reveal the secret exactly once through a modal that
  blocks dismissal until the operator acknowledges they have saved
  the value.
- **Additive migrations 077, 078, 079.** No drops, no renames.

### Operator action required

None. The new endpoints are off-network until an admin creates a
service account and mints a key from the UI.

## v0.13.2

Polish on the installer UX and one resiliency fix: removes a confusing
duplicate-line render on the VM, swaps the success glyph for a real
checkmark, and retries IAM bindings that race against GCP's service-
account propagation.

### Changes

- **No more duplicated `[ ] / [v]` steps on the VM.** When `install-gcp.sh`
  hands off to the VM via `gcloud compute ssh --command=...`, the remote
  shell has no TTY, so `installer/output.sh`'s `_io_is_tty` returned
  false and `_io_step_start` fell through to a `printf '\n'` branch. The
  result was every step rendering twice: once in-progress, once final.
  In non-TTY mode `_io_step_start` is now a no-op; `_io_step_end` still
  emits the final state once. TTY mode (`install-gcp.sh` on the
  operator's laptop) is unchanged: the in-progress line still animates
  in place, then is overwritten by the final state.
- **Success glyph is now `[✓]` instead of `[v]`.** Both `install-gcp.sh`
  and `bioaf setup` share `installer/output.sh`, so both pick this up.
  U+2713 renders cleanly in every modern terminal font; the rest of the
  glyph set stays ASCII so failures and warnings copy-paste verbatim
  into bug reports.
- **Retry IAM bindings on SA-propagation lag.** Newly created service
  accounts can take 5-30 seconds before the project IAM endpoint sees
  them, even after `wait_for_sa` (which polls the SA endpoint) returns.
  When that happens, `gcloud projects add-iam-policy-binding` exits with
  `INVALID_ARGUMENT: Service account ... does not exist` and aborts the
  install. A new `retry_iam` wrapper catches that specific error,
  sleeps 5s, and retries up to twice (3 attempts total) before failing
  the step. Wraps all nine `add-iam-policy-binding` calls in the
  bootstrap/app/reader SA setup and the bioaf-managed tag binding.

### Operator action required

None. Re-run `install-gcp.sh` if a prior run failed on the SA
propagation race; the retry now absorbs it.

## v0.13.1

Closes two automation gaps in v0.13.0's at-rest encryption rollout.
After this release, no operator on any deployment topology has to
touch a CLI or SSH to land or run v0.13.x: keys are generated by the
chart on first `helm install`, and by the backend container itself on
docker-compose upgrades from any pre-v0.13.0 version.

### Changes

- **Helm: auto-generated encryption Secret.** New
  `helm/bioaf/templates/secret-encryption.yaml` creates the Secret on
  first `helm install` using `randBytes 32` shaped into a urlsafe-base64
  Fernet key (same shape as `cryptography.Fernet.generate_key()`). On
  subsequent upgrades, `lookup` finds the existing Secret and the chart
  leaves it alone, so the key never silently rotates. The Secret is
  annotated `helm.sh/resource-policy: keep` so a `helm uninstall` does
  not delete it (losing the key value loses every encrypted DB column).
- **Backend entrypoint auto-bootstrap.** Fixes the upgrade-from-<0.13.0
  path. The host-side `ensure-encryption-key` step v0.13.0 added to
  `cmd_update` does not run on those upgrades, because bash parses the
  pre-v0.13.0 `bioaf` script into memory before the `git checkout`
  brings v0.13.0's version onto disk. To recover, the v0.13.1 backend
  image's entrypoint now self-checks `BIOAF_ENCRYPTION_KEYS`. If it is
  empty and the host's `docker/.env` is mounted writable (which the
  v0.13.1 `docker-compose.yml` now does at `/host/.env`), the
  entrypoint generates a Fernet key, appends it to the host `.env`, and
  exports it for the current process. Subsequent container restarts
  pick it up through compose's env-file expansion. Refuses to overwrite
  a non-empty existing value in `.env` (defense in depth) and exits
  loudly if the mount is missing or read-only.
- **Operator-owned keys still supported.** Pre-creating the Helm Secret
  or pre-populating `BIOAF_ENCRYPTION_KEYS` in `docker/.env` is detected
  and respected; the chart and entrypoint both skip auto-generation.

### Operator action required

None on any deployment topology. After upgrading, the new key value can
be inspected:

- **docker-compose:** `grep BIOAF_ENCRYPTION_KEYS docker/.env`
- **Kubernetes:**
  `kubectl get secret bioaf-encryption -o jsonpath='{.data.keys}' | base64 -d`

Back the value up separately from your DB dump. See
`decisions/ADR-047-data-at-rest-encryption.md`.

## v0.13.0

Encrypts sensitive database columns at rest so `pg_dump` exposure no
longer leaks plaintext secrets. See `decisions/ADR-047-data-at-rest-encryption.md`
for the full design.

### Changes

- **App-level Fernet encryption.** New `app/services/encryption_service.py`
  wraps `cryptography.fernet.MultiFernet`; rotation-ready from day 1.
  A new SQLAlchemy TypeDecorator (`EncryptedString`) transparently
  encrypts/decrypts on bind/result so ORM code stays plaintext-only.
- **Sensitive columns now ciphertext at rest.** Migration 076 widens
  storage to TEXT where needed and backfills Fernet tokens for
  `organizations.{smtp_password, slack_client_secret,
  slack_signing_secret}`, `session_credentials.ssh_private_key`,
  `compute_sessions.heartbeat_token`, `slack_installations.bot_token`,
  `slack_webhooks.webhook_url`, and the `gcp_service_account_key` row
  in `platform_config`. The migration is idempotent (already-encrypted
  rows are skipped) and refuses to run without `BIOAF_ENCRYPTION_KEYS`.
- **Centralized platform_config access.** `PlatformConfigService.get`
  / `set` / `get_many` is now the single read/write path for
  `platform_config`. Sensitive keys are encrypted on write and
  decrypted on read; non-sensitive keys pass through unchanged. All
  raw SELECTs of the GCP SA key were routed through the new service.
- **`BIOAF_ENCRYPTION_KEYS` generated by `install.sh`.** First run
  writes a Fernet key into `docker/.env`. `--force` regenerates it
  (with a warning that existing encrypted columns become unreadable).
  Helm reads the value from a Kubernetes Secret named in
  `values.yaml` (`encryption.secretName`).
- **Startup fail-fast.** `validate_encryption_keys()` mirrors
  `validate_jwt_secret()`: missing or malformed keys halt the
  container immediately rather than serving traffic with broken
  encryption.

### Operator action required

- Before deploying v0.13.0 to Kubernetes, create the encryption Secret:

  ```bash
  kubectl create secret generic bioaf-encryption \
    --from-literal=keys="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  ```

  Back the value up *separately* from the dump bucket; losing it
  makes encrypted columns unrecoverable. See
  `documentation/recovery-and-encryption.md`.
- For docker-compose installs, run `./install.sh generate-env` (or a
  full `./install.sh`); the key is added to `docker/.env` automatically.
- For existing docker-compose installs upgrading via `./bioaf update`:
  the updater auto-appends a generated `BIOAF_ENCRYPTION_KEYS` line to
  the existing `docker/.env` before restarting containers (the rest of
  the file is left untouched). It will print a yellow notice with the
  key so operators can capture it for backup. You can also pre-generate
  the key by running `./install.sh ensure-encryption-key` from the
  install directory before `./bioaf update`.

## v0.12.1

Hardens the `install-gcp.sh` bootstrapper in two ways.

### Changes

- **gcloud version gate.** A new "Step 1b" check inspects
  `gcloud version --format=json` and, if any installed component is
  out-of-date, asks the user whether to update. If they accept, the
  installer runs `gcloud components update --quiet` (with a manual
  fallback for apt/snap/brew-cask installs that disable in-place
  updates). If they decline, the installer compares the current
  versions against the minimums bioAF has tested against
  (Google Cloud SDK 563.0.0, alpha/core 2026.03.27, bq 2.1.31,
  gcloud-crc32c 1.0.0, gke-gcloud-auth-plugin 0.5.12, gsutil 5.36)
  and aborts if any component is below. If versions already meet the
  minimums and no updates are available, the step is silent.
- **Conservative quota auto-request.** The Cloud Quotas helper now
  submits a `QuotaPreference` only when it has successfully read the
  current limit and confirmed it is below the target. Previously a
  failure to read (older gcloud, missing alpha component, parse
  error, region not in the response) was treated as "current = 0",
  which triggered an unnecessary increase request. The helper now
  returns empty on those paths and `bioaf_quota_ensure_all` prints
  a "could not read current limit; request manually if needed"
  message instead.
- **Structured per-step installer output.** Both `install-gcp.sh`
  (laptop side) and `bioaf setup` (VM side) used to emit hundreds of
  lines of `gcloud` / `docker` chatter during a fresh install,
  making it hard to see what was happening or whether anything had
  failed. A new shared helper library at `installer/output.sh` wraps
  each operational command and renders a one-line status:
  `[v]` for success (green), `[x]` for failure (red, with the last
  20 lines of the log dumped inline), or `[o]` for warnings (yellow,
  collected for an end-of-run summary). Full stdout/stderr from the
  wrapped commands is captured to `~/.bioaf/install-gcp.log` and
  `~/.bioaf/install.log` respectively; pass `--verbose` to keep the
  output inline when debugging. Cloud Logging and update-agent
  install remain fail-fast since both are critical for production
  observability and in-app self-update.
- **Manual worksheet ordering.** The "abort or choose manual"
  fallback in `install-gcp.sh` now copies the prefill file to the
  VM **before** telling the user to SSH in. The prior ordering
  (SSH first, then `scp`) was impossible to follow from inside the
  SSH session.

## v0.12.0

Adds a browse-and-install flow for the full nf-core pipeline catalog.
Previously the pipeline catalog only surfaced four hardcoded entries
(scrnaseq, rnaseq, fetchngs, bioaf-system-test) and adding anything
else required a code change or a hand-built Git URL. Admins can now
open a "Search Available Pipelines" modal from `/pipelines/catalog`,
filter the ~150 published nf-core pipelines by name, description, or
topic, pick a version from the release dropdown, and install in one
click. Pipelines that have a newer release upstream show an "Update
available" badge and update in-place; existing pipeline runs keep
their pinned version.

### New features

- **nf-core registry cache.** A new background task refreshes
  `https://nf-co.re/pipelines.json` once per day and stores the
  result in `nf_core_registry_pipeline` (migration 075). The cache
  is global; install state is computed per organization by joining
  `pipeline_key = 'nf-core/' || name` against `pipeline_catalog`.
  Fetch failures preserve the cached rows and write the error to a
  singleton `nf_core_registry_refresh` tracker so the browse modal
  keeps working when nf-co.re is unreachable.
- **Search Available Pipelines modal.** Reachable from the new
  button on `/pipelines/catalog` (gated on `pipelines:view`). Shows
  name, description, star count, topics, and a status chip
  ("Installed v X.Y", "Update available", "Not installed",
  "Archived"). Install opens a sub-step with a version dropdown that
  defaults to the latest release; the `dev` pseudo-release is
  filtered out. Admins (any user with `pipelines:create`) also see
  a "Refresh registry" button and the "last refreshed" timestamp.
- **One-click version updates.** When the installed version of a
  catalog entry differs from the registry's latest release, the
  modal renders an "Update to v X" button that bumps the version
  through the existing `PATCH /api/pipelines/version/{key}` endpoint
  and re-fetches `nextflow_schema.json` so the parameter UI picks up
  any new flags.
- **Per-pipeline QC template mapping.** Imported nf-core pipelines
  get a QC template assigned automatically (`scrnaseq -> scrnaseq`,
  `rnaseq -> rnaseq`, everything else `generic`). Admins can change
  the template after install via the existing catalog settings.

### Internal changes

- Four new routes under `/api/pipelines/registry/...` (browse,
  versions, install, manual refresh), declared before the existing
  `/{key:path}` route so FastAPI's path converter does not capture
  them.
- New service `NfCoreRegistryService`
  (`refresh_registry`, `list_pipelines_with_status`,
  `get_pipeline_versions`, `install_pipeline`) reuses
  `PipelineCatalogService.fetch_pipeline_schema` rather than
  duplicating the GitHub raw-content fetch.
- The hardcoded `BUILTIN_PIPELINES` list stays as-is for new-org
  backfill; the new import flow runs alongside it.

## v0.11.15

Makes the Nextflow report tab actually render: plots, the tasks table,
and the report's internal Summary / Resources / Tasks navigation all
work now. Also fixes a progress-bar regression where pipelines that
legitimately run the same process more than once on the same input
tag (nf-core/scrnaseq's `MTX_TO_H5AD`, `MTX_TO_SEURAT`) were
under-counted in the run-detail stats bar.

### Bug fixes

- **Nextflow report renders correctly inside the iframe.** The report
  bundles Plotly, which JITs vector math via `new Function(...)`. Our
  CSP `script-src` allowed `'unsafe-inline'` but not `'unsafe-eval'`,
  so the very first `Plotly.newPlot` threw a CSP violation and every
  plot div stayed empty. `srcdoc` iframes inherit their parent's CSP
  per the HTML spec, so `sandbox` doesn't help here: the only fix is
  to add `'unsafe-eval'` to the parent CSP (`nginx.conf` and the
  FastAPI security headers middleware both updated).
- **Report's Summary / Resources / Tasks navigation no longer
  "refuses to connect."** A `srcdoc` iframe inherits its base URL
  from the parent document, so `<a href="#tasks">` inside the report
  resolved to `<parent_url>#tasks` and clicking it triggered a
  cross-document navigation that the parent's
  `frame-ancestors 'none'` policy blocked. A tiny capture-phase
  click handler is now injected into the report HTML server-side: it
  intercepts hash-only anchor clicks and does in-iframe
  `scrollIntoView` instead. Bootstrap `data-toggle` anchors (the
  Raw Usage / % Allocated sub-tabs) are passed through untouched.
- **Stop the report from 404-ing nextflow.io's favicon.** The
  Nextflow HTML ships with `<link rel="icon" href="nextflow.io/...">`,
  which our `img-src 'self' data:` CSP correctly blocked but which
  also produced a noisy console error. Favicons aren't shown for
  `srcdoc` iframes anyway, so strip the link tag in
  `get_run_report` before returning.
- **Pipeline progress no longer under-counts parallel tasks.** The
  v0.11.13 dedup keyed on the trace's `name` column to collapse
  preempted retries; that mis-collapsed pipelines like
  nf-core/scrnaseq that legitimately run the same process multiple
  times on the same input tag to produce different output artifacts
  (`MTX_TO_H5AD` on SAMPLE-101 runs 3x: raw, filtered, custom-empty-
  drops). A 17-task run was being reported as 13/13. Dedup now keys
  on `task_id` (Nextflow's canonical task identifier across retries)
  and takes the row with the highest `attempt` as the final state.
- **bioAF tab favicon.** `/favicon.ico` was 404-ing because no asset
  existed for it. Same icon used on bioaf-site is now bundled into
  the Next.js app and served at `/favicon.ico` automatically.

## v0.11.14

Fixes a class of GKE deploy hang where the throwaway default node pool
the cluster creates at bootstrap (and `remove_default_node_pool=true`
nukes seconds later) would wait up to 70 minutes for capacity in every
regional zone before GKE gave up, then Terraform timed out at 40
minutes and the cluster ended up in `RUNNING_WITH_ERROR`. Random
resource suffixes don't help here, since they don't change per-zone
GCE capacity. The deploy now runs a pre-flight capacity probe and pins
the throwaway default pool to one zone that just accepted a real
instance insert.

### Bug fixes

- **Cluster bootstrap no longer hangs on a single-zone GCE stockout.**
  Before this change, a regional Standard cluster fanned its
  `initial_node_count = 1` default pool across every zone in the
  region. If any one zone was out of `e2-medium` capacity, that zone's
  per-zone IGM hung indefinitely (per-zone IGMs don't fall back across
  zones), and the cluster ended up in `RUNNING_WITH_ERROR` after a
  ~40-minute Terraform timeout. The real node pools were never
  affected (they set their own `node_locations` with
  `location_policy = "ANY"`), only the implicit bootstrap pool.
- **Probe failure short-circuits the deploy instead of burning 40
  minutes.** If every regional zone is stocked out at probe time, the
  deploy now surfaces a `stack_error` with the zones tried, and never
  starts `terraform apply` for the compute module.

### Enhancements

- **Pre-flight GCE capacity probe before the compute terraform apply.**
  `stack_deployment` now iterates the regional zones and attempts a
  real `compute.instances.insert` in each. The first zone that does
  not return `ZONE_RESOURCE_POOL_EXHAUSTED` / `GCE_STOCKOUT` wins, the
  probe instance is deleted, and the selected zone is written to
  `platform_config.gke_default_pool_zone`. Terraform reads that into a
  new compute module variable that constrains the cluster's
  `node_locations` to that single zone. The four real node pools
  (system / pipelines / interactive / pipeline-head) set their own
  `node_locations` from `k8s_node_zones` and are unchanged: the
  cluster stays regional, real pools keep multi-zone fallback.
- **Probe progress is visible in the deploy modal.** The UI shows
  "Checking GCE zone capacity for cluster bootstrap..." then "Selected
  zone us-central1-X for cluster bootstrap (has capacity)." before the
  existing compute progress events.

## v0.11.13

Fixes a confusing progress count on the pipeline-run page. After a run
with Spot preemptions, the bar would read "17/20 / 85%" on a fully
successful pipeline because each preempted-then-retried task was being
counted as a separate process.

### Bug fixes

- **Step retries no longer inflate the process total.** The progress
  counter now dedupes the Nextflow trace by process name and reports
  unique pipeline steps. A 17-step pipeline that had 3 task attempts
  preempted and retried now reads "17 / 17 succeeded" with a full bar.

### Enhancements

- **Step retries surface in the run header.** When a run had retries,
  the stats bar (Started · Completed · Duration) now includes a "Step
  retries" counter. Clicking it opens a modal listing each step that
  was retried and how many attempts it took. The counter is hidden when
  a run had no retries, so clean runs stay clean.

## v0.11.12

Fixes a follow-up to v0.11.11: pipeline task pods reached Fusion, but Fusion
failed to mount the GCS work directory because `roles/storage.objectAdmin`
doesn't include `storage.buckets.get`. Tasks exited 126 before
`.command.sh` could run. Also fixes the pipeline-run page's log panel,
which silently stopped auto-refreshing once the head pod was scheduled.

### Bug fixes

- **Pipeline-runner gets bucket-level access on `bioaf-*` buckets.**
  The binding on `bioaf-pipeline-runner` now uses `roles/storage.admin`
  (still scoped to `bioaf-*` buckets via IAM Condition, matching how
  `bioaf-app` is scoped in `install-gcp.sh`). Fusion can now perform
  the bucket lookup it needs to mount `gs://bioaf-raw-*` as a local
  filesystem inside task pods.
- **Pipeline-run logs auto-refresh every 5 seconds.** The pipeline-run
  detail page already polled run metadata every 10s, but logs only
  reloaded when `k8s_job_name` flipped (typically once per run), so
  watching a live pipeline required manual page refreshes. A sibling
  interval now reloads logs while the run is `running` or `pending`
  and the logs tab is open, stopping automatically on terminal status.
  Background polls suppress the loading spinner so the log `<pre>`
  stays mounted and the user's scroll position survives each refresh
  (the spinner only flashes on the user-visible initial load).
- **Pipeline head + task pods pinned against autoscaler eviction.**
  Long pipelines (e.g. STAR_GENOMEGENERATE for human GRCh38, ~45 min)
  were occasionally killed mid-run when GKE's cluster autoscaler
  decided their node was underutilized and scaled it down. Both the
  Nextflow head Job and the task pods spawned by Nextflow's K8s
  executor now carry
  `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` so the
  autoscaler leaves them in place for the run's duration. Nodes are
  still reclaimed normally after pods terminate.
- **Nextflow head pod isolated from Spot preemption.** The
  `bioaf-pipelines` node pool runs on Spot for cost; any VM in the
  pool can be reclaimed by GCP with a 30-second SIGTERM regardless of
  pod annotations (Spot preemption is not subject to
  `cluster-autoscaler.kubernetes.io/safe-to-evict`). Long pipelines
  whose head pod happened to land on a preempted VM were killed
  mid-run -- verified empirically with the
  `compute.instances.delete` -> immediate MIG-replacement pattern.
  A new on-demand `bioaf-pipeline-head` pool now hosts the head Job
  via `nodeSelector + toleration`. Task pods stay on the Spot
  `bioaf-pipelines` pool (Nextflow already retries preempted tasks
  via the existing errorStrategy on exit 143/137/247). The new pool
  is tainted so Nextflow's task pods, which can't carry custom
  tolerations, never accidentally land there.
- **Per-submit GCS path uniqueness.** Nextflow reports, traces, and
  persisted pipeline logs were keyed by `bioaf-pipeline-{run_id}`, so
  if the `pipeline_runs.id` sequence was reset (e.g., during a clean
  demo wipe), a new run could read or be confused by a stale
  `report.html` left in GCS by an earlier run with the same recycled
  ID. `job_name` now embeds a per-submit epoch suffix
  (`bioaf-pipeline-{run_id}-{epoch}`) which becomes the K8s Job name,
  the GCS report/trace/log prefix, and is stored in
  `pipeline_runs.k8s_job_name` for read consistency. Two submits with
  the same `run_id` no longer collide.

### Deploy

Apply the compute Terraform module from Infrastructure -> Components
(any trivial cluster-config save triggers `terraform plan + apply` on
the compute module). The plan should show one in-place change to
`google_project_iam_member.pipeline_runner_storage` (role
`storage.objectAdmin` -> `storage.admin`), zero destroys. Resubmit any
pipeline runs that failed under v0.11.11.

## v0.11.11

Fixes a Workload Identity gap that left Nextflow pipeline pods with no GCP
identity, so launches against any pipeline (nf-core/scrnaseq, etc.) failed
at the first GCS read with `storage.objects.get denied`.

### Bug fixes

- **Pipeline pods now get a real GCP identity.** The bioaf-pipelines node
  pool enforces GKE_METADATA, so pods must bind a GCP service account via
  Workload Identity to read or write GCS. A new `bioaf-pipeline-runner`
  GSA is created by the compute Terraform module, granted
  `roles/storage.objectAdmin` scoped to `bioaf-*` buckets (matching how
  `bioaf-app` is scoped in the installer), and wired to the
  `bioaf-pipelines/bioaf-pipeline-runner` KSA via
  `roles/iam.workloadIdentityUser`. The backend now stamps the
  `iam.gke.io/gcp-service-account` annotation on the KSA at namespace
  setup and patches it on the upgrade path so existing deployments
  recover without recreating the namespace.

### Deploy

Apply the compute Terraform module from the Infrastructure -> Components
page (any trivial cluster-config save triggers `terraform plan + apply`
on the compute module). The plan adds three additive resources
(`google_service_account.pipeline_runner`,
`google_project_iam_member.pipeline_runner_storage`,
`google_service_account_iam_member.pipeline_runner_workload_identity`)
with no destroys. After the apply finishes, wait a minute or two for the
Workload Identity binding to propagate, then resubmit any pipeline runs
that failed under the older version.

## v0.11.10

Adds inline file upload to the experiment Files tab, so users no longer
have to leave the experiment to attach files.

### Enhancements

- **Upload from the experiment Files tab.** A new Upload toggle next
  to the Type/Source filters expands a drag-and-drop panel, scoped
  to the current experiment. An optional sample selector lets the
  user pin the upload to a specific sample within the experiment;
  otherwise the file is associated with the whole experiment. The
  rename-suggestion UX from Data & Files > Upload comes with it.
- **Search-by-filename in the experiment Files tab.** The filter set
  now matches the global Files page (minus Project and Experiment,
  which are already implied by the experiment scope).

## v0.11.9

Decouples the deployed image tag from the worktree version, so a stray
`git pull` followed by `bioaf restart` can no longer silently switch
which version is running.

### Bug fixes

- **`bioaf start` / `bioaf restart` no longer change the running
  version.** The `BIOAF_IMAGE_TAG` env var was previously recomputed at
  every `start` from `backend/pyproject.toml`. After `git pull`, a
  routine restart would pull and start the new image even though the
  user had not asked to upgrade. The active tag is now persisted in
  `docker/.env` (`BIOAF_IMAGE_TAG=v…`) and only the commands that
  legitimately change the deployed version (`setup`, `update`, `build`)
  may write to it. `start` and `restart` only consume it.

### Migration

Legacy installs that don't yet have `BIOAF_IMAGE_TAG=` in `docker/.env`
get the pin auto-bootstrapped on first start: the script reads the
running backend container's image tag and writes it to `docker/.env`,
falling back to the on-disk version only if no container exists. This
runs once; subsequent starts use the pinned value.

## v0.11.8

Fixes the in-app "Check for updates" control on the Platform Info page.

### Bug fixes

- **"Check for updates" now actually re-queries GitHub.** The control
  was a small text link at the bottom of the page that hit
  `/api/upgrades/check`, but that endpoint serves a 1-hour in-memory
  cache. A user supporting someone over the phone ("a new version is
  out -- click the button") would see the same stale "latest version"
  no matter how many times they clicked. The endpoint now accepts
  `?force=true` and the button passes it; routine page loads and the
  daily background poll continue to use the cached path.
- **"Check for updates" is now a real button next to the version
  display.** Previously it was small linkified text at the very bottom
  of the card, easy to miss and not obviously interactive.

### Migration

None. No schema changes.

## v0.11.7

Hardens the in-app update flow against two failure modes that surfaced
when v0.11.6 was published, and renders the changelog on the Platform
Info page as actual markdown.

### Bug fixes

- **Update no longer no-ops after a partial failure.** `cmd_update` in
  `bioaf` previously read the source version from `backend/pyproject.toml`
  on disk. A failed pull (e.g. when release images are not yet
  published) leaves the worktree checked out at the new tag while the
  containers still run the old version, so the next attempt would
  short-circuit with "Already running version <target>" and never
  actually deploy. The script now reads the running backend
  container's image tag via `docker inspect` and only falls back to
  disk when no container is up.
- **Update aborts cleanly when release images are still publishing.**
  A pre-flight check now HEADs the manifest for `bioaf-backend`,
  `bioaf-frontend`, and `bioaf-cellxgene` on `ghcr.io` before any
  destructive step (backup, checkout, pull). If any image is missing,
  the update writes a friendly status -- "Release images for v… are
  not yet published. The release publish workflow may still be in
  progress -- please try again in a few minutes." -- and exits without
  touching the worktree.
- **Platform Info changelog renders as markdown.** GitHub release
  bodies (with `###` headings, `**bold**`, inline code, bullets) were
  printed as raw text. Now rendered with `react-markdown` + GFM and
  styled via Tailwind's typography plugin, which is added globally so
  any future markdown surfaces can use the `prose` class without
  reinstalling per-use.

### Migration

None. No schema changes.

## v0.11.6

Fixes a cluster of bugs in the experiment / template / sample creation
flow and brings the experiment-template UI closer to parity with the
experiment-creation form.

### Bug fixes

- **Sample detail modal now displays custom fields.** The
  `/api/experiments/{id}/samples` and single-sample endpoints were
  silently dropping the `custom_fields` list when constructing
  `SampleResponse`, even though values were persisted in
  `sample_custom_fields`.
- **Experiments created from a template now inherit the template's
  required and custom fields.** `ExperimentService.create_experiment`
  previously stored `template_id` on the experiment but never read the
  template's `required_fields_json` or `custom_fields_schema_json`.
  Templates' required sample fields are now copied as
  `experiment_field_defaults` rows with `is_required=true`, and custom
  field schemas are copied as `experiment_custom_fields` rows with
  `field_type` and `is_required` preserved. User-supplied
  `default_value` / `field_value` still wins.
- **The "Required" indicator now renders for custom fields on the
  experiment Overview.** The detail-response builder was constructing
  `CustomFieldResponse` without `is_required`, so the flag always read
  `false` regardless of database state.

### New features

- **GSheet column aliases persist across sample imports.** When mapping
  GSheet headers to sample fields or custom fields during experiment
  creation, the mapping is now stored in `experiments.column_aliases`
  (additive JSONB column, migration `074`). Subsequent sample-import
  preview and confirm endpoints consult these aliases as a fallback
  after user-supplied mappings, so re-importing the same sheet routes
  columns correctly without re-mapping. User mappings still take
  priority over aliases.
- **Template form now exposes all defaultable sample fields.**
  Previously only 5 of the 10 defaultable fields were available;
  `sample_batch_code`, `sequencing_batch_code`, `molecule_type`,
  `library_prep_method`, and `library_layout` are now selectable.
- **GSheet import for templates.** A new "Import from Google Sheet"
  flow on the template form reads sheet headers via
  `/api/v1/sheets/preview` and lets the user mark recognized columns
  as required, route unknown columns to existing sample fields, or add
  them as typed custom fields with required toggles.

### Migration

- `074_add_column_aliases_to_experiments` adds a nullable
  `column_aliases` JSONB column to the `experiments` table. Additive
  only; safe to apply on existing installs.

## v0.11.5

Repository move from the personal `not-that-guy-again/bioAF` namespace
to the `bioAF` GitHub organization. Code-only point release: no schema
changes, no migration required, no behavior changes for end users.

### What changed

- Updated all references in code, scripts, Dockerfiles, docs, and
  workflows from `not-that-guy-again/bioAF` to `bioAF/bioAF` (HTTPS and
  SSH clone URLs, raw content URLs, GitHub API URLs)
- Updated container image references from
  `ghcr.io/not-that-guy-again/bioaf-{backend,frontend,cellxgene}` to
  `ghcr.io/bioaf/bioaf-{backend,frontend,cellxgene}`
- Updated the in-app upgrade check (`backend/app/services/upgrade_service.py`)
  to poll the new repo's releases endpoint
- Updated OCI `org.opencontainers.image.source` labels in all three
  Dockerfiles to point at the new repo

### Notes for existing installs

Existing installs continue to pull old images from
`ghcr.io/not-that-guy-again/...` and will keep working. Once an install
is upgraded to v0.11.5, subsequent `docker compose pull` operations
will fetch from the new `ghcr.io/bioaf/...` namespace. Both image sets
exist for the v0.8.x through v0.11.4 tag range; v0.11.5 and later are
published only to the new namespace.

The in-app updater begins checking the new repo's releases on first
run after upgrade. GitHub redirects also keep the old endpoint working
transparently for any installs that lag.

## v0.11.4

Point release that adds a dedicated `bioaf-system` GKE node pool to host
the cluster's system addons (calico-typha, fluentbit, gmp-operator,
gke-metadata-server, etc.) on its own infrastructure rather than
piggy-backing on whichever user pool happens to be alive. Touches GKE
topology only -- no schema changes, no migration required.

### `bioaf-system` always-on pool

Resolves the dedicated-system-pool follow-up flagged in to-resolve.md's
second-round status. Before this release, calico-typha's 2-replica
anti-affinity forced the `bioaf-pipelines` pool to spin up *two*
`n2-highmem-16` nodes whenever any pipeline ran -- one for the pipeline
pod, one purely for system addons sitting at ~10% disk utilization and
otherwise wasted. Pipelines and interactive pools could not actually
scale to zero between runs, because cluster-scoped Deployments
(calico-typha, kube-dns, konnectivity-agent, etc.) needed a host.

The new `bioaf-system` pool gives those addons a dedicated home:

- 1 always-on `e2-standard-2` node (2 dedicated vCPU, 8 GiB RAM)
- 30 GB `pd-standard` boot disk (does not consume `SSD_TOTAL_GB` quota
  the way `pd-ssd` would)
- On-demand, not spot -- system addons must not be evicted at random
- `total_min_node_count = 1` / `total_max_node_count = 2` with
  `location_policy = "ANY"`, so the autoscaler places the floor node
  in whichever zone has `e2-standard-2` capacity at deploy time
  (regional cluster, not multi-zone-redundant -- HA was never an
  architected goal for cluster workloads, see commits `bde4d604` and
  `c399ee21`)
- No taint: GKE-managed DaemonSets do not reliably tolerate custom
  taints. The pool is sized so Nextflow process pods do not fit and
  fall back to the pipelines pool by resource constraint instead.

With the system pool taking the addon load, both `bioaf-pipelines` and
`bioaf-interactive` now genuinely scale to zero between workloads.
Cost: ~$25/mo for the always-on system pool, replacing what was
previously a ~$70-100/mo wasted node held by addons on the pipelines or
interactive pool.

Two new module variables (`k8s_system_machine_type`, `k8s_system_max_nodes`)
expose machine-type and max-node tuning while keeping the always-on
floor at 1. They are not yet wired into `platform_config` or the
deploy wizard -- defaults take effect on every install. A future
release may surface them as advanced options.

### How sizing landed where it landed

The right machine type took three iterations and is worth recording so
the next person tuning this does not relearn it the hard way:

- **e2-small** (first attempt): 940m CPU / 1.4 GiB allocatable. Pinned
  the autoscaler at max=2 in every active zone -- one CPU-saturated
  node could not host the full DaemonSet set, so a second came up.
  Total: 4 nodes.
- **e2-medium** (second attempt): same 940m CPU allocatable, just more
  memory. Both `e2-small` and `e2-medium` are *shared-core* burstable
  types: they advertise 2 vCPU max but baseline-share 0.5 / 1.0 vCPU
  respectively, and Kubernetes treats only the burstable max as
  `allocatable`. The autoscaler packs them identically. Same 4-node
  outcome.
- **e2-standard-2** (final): 2 *dedicated* vCPU, 8 GiB RAM, ~1.9 vCPU
  allocatable. One node per zone fits the addon set. Combined with
  `total_min_node_count`/`total_max_node_count` (global counts rather
  than per-zone), drops to 1 node total.

Use of `total_*_node_count` instead of `min_node_count`/`max_node_count`
is what allows a regional pool to honor "1 node, anywhere with capacity"
semantics. Per-zone counts would have multiplied the floor across every
zone in `node_locations`.

### What's still outstanding from to-resolve.md

- **Issue #1 final mile**: the public `bioaf-base` GCE work-node image
  still needs to be built and published. Backend seeder is in place
  and gated on `BIOAF_BASE_WORK_NODE_IMAGE_URI`, so this is a one-time
  operator task.
- **Issue #3 bonus**: `_extract_pod_termination_info` does not yet
  surface `Unschedulable: ...` reasons (e.g. `QUOTA_EXCEEDED`,
  `pod didn't trigger scale-up`) in the run-log view. Will be a
  separate follow-up branch.

## v0.11.3

Point release that automates the GCP quota-increase requests bioAF
needs at install time, plus a small refresh of the shell test suite
that had drifted away from the current `bioaf` and `install.sh`
contracts. No schema changes; no migration required.

### Auto-quota-request in `install-gcp.sh`

Resolves the last open item from to-resolve.md issue #3 (CPUS) and
its 2026-05-07 expansion (SSD / DISKS). Fresh GCP projects ship with
quotas too tight for bioAF to schedule even one pipeline pod
(12 vCPUs, 250 GB regional SSD). On v0.11.2 and earlier, the user
had to discover this via `Pending` pods and `QUOTA_EXCEEDED` autoscaler
events, then go to the console to file an increase manually.

`install-gcp.sh` now adds a "Step 5b: GCP Quota Auto-Request" right
after region selection. It checks each of the three quotas bioAF
needs and, for any that are below target, files a `QuotaPreference`
through the Cloud Quotas API:

- `CPUS-ALL-REGIONS-per-project` &rarr; 64
- `SSD-TOTAL-GB-per-project-region` &rarr; 1024
- `DISKS-TOTAL-GB-per-project-region` &rarr; 2048

On a paid billing account the API auto-approves these in seconds and
the installer reports "granted automatically." On a free-trial
billing account the request goes to human review (1-2 business days)
and the installer surfaces a clear "Google needs to review and
approve this -- this is normal" message before continuing. The
install never aborts on a quota request; if a request was actually
denied, the affected pipeline run will surface the underlying
`QUOTA_EXCEEDED` reason in its log.

The logic lives in `installer/quota.sh` (sourced by `install-gcp.sh`
from a local clone, or fetched over HTTPS when the script is
`curl|bash`'d). Cloud Quotas API errors are surfaced verbatim, so a
4xx response shows the API's `code`/`status`/`message` rather than
just an opaque "request failed."

### Stale `bats` shell tests refreshed

`tests/shell/test_bioaf.bats` and `tests/shell/test_install.bats`
hadn't been touched since the original installer commit, but the
underlying scripts had moved on across many releases. Three tests
were checking for behavior that no longer exists:

- `bioaf help` expected `create-admin`, but admin creation moved to
  the web wizard; the test now checks the actually-current commands.
- `install.sh check-prereqs` expected exit 0 on any host with docker
  and git, but `install.sh` now refuses to run on macOS / Windows by
  design (it is meant for the GCP Linux VM); the test now skips on
  non-Linux hosts.
- `install.sh generate-env` was tested as a "refuse to overwrite"
  gate, but the current contract is "regenerate the file but
  preserve known values (POSTGRES_PASSWORD, SECRET_KEY) unless
  `--force` is passed"; the test now pins that contract.

No production code changed -- this section is test-only cleanup.

### Bug fix: install-gcp.sh exited silently on a Cloud Quotas 4xx

The first version of the auto-quota-request flow (built earlier on
this branch) used `curl -fsSL` and propagated curl's non-zero exit
through the `pref_id=$(bioaf_quota_request_increase ...)` assignment
in the orchestrator. Under `set -euo pipefail` (which `install-gcp.sh`
uses) that aborted the entire installer mid-flow with no user-visible
error, right after printing "Requesting an automatic quota increase
from Google..." Two regression tests now pin this down; the helper
always returns 0 and signals failure via empty stdout, matching the
convention `bioaf_quota_poll` already used.

### Bug fix: missing `contactEmail` rejected every QuotaPreference

After the silent-abort fix surfaced the underlying API error, fresh
projects revealed a second issue: the Cloud Quotas API requires a
`contactEmail` field on every `QuotaPreference` body ("Contact email
must be set in order to increase quota value") and the helper was
not sending it. Long-lived projects can quietly accept submissions
without it because of contacts retained from prior console activity,
which masked the requirement during early testing.

The orchestrator now resolves the gcloud-active account once
(`gcloud config get-value account` -- same identity as the bearer
token, so Google can email the right human if review or denial
happens) and threads it into the body. `(unset)` is treated as empty
so installs without a configured account degrade cleanly rather than
sending a literal `(unset)` string as the contact.

## v0.11.2

Bug-fix point release covering the environment-management gaps and a
work-node Packer-build quota issue surfaced while testing v0.11.1
end-to-end on a fresh greenfield install. No schema changes; no
migration required. The bioaf-base work-node seed is gated by
`BIOAF_BASE_WORK_NODE_IMAGE_URI` and is a no-op until the public
image is published, so existing installs are unaffected.

### Environment pickers correctly filter by type

The Notebooks page and the Workbench Environments "All" filter both
showed environments of the wrong type -- pipeline envs leaked into
notebook pickers and into the workbench list, where they could not
actually be selected. Tracked as to-resolve.md issue #2.

- `notebooks/page.tsx` now requests `/api/v1/environments?type=notebook`.
- The Workbench Environments "All" filter fetches `notebook` and
  `work_node` lists separately and merges them; pipeline envs are
  excluded.

### Default environments seeded at bootstrap

Two related gaps blocked first-time users from launching anything
without manual env creation:

- A default notebook env seeder was missing alongside the existing
  pipeline / work_node seeders; the notebooks picker came up empty.
  Added `ensure_default_notebook_environment` mirroring the pipeline
  seeder.
- Seeders only ran from the lifespan startup hook, which skips when
  org / admin do not yet exist. On a fresh install the user
  completes bootstrap *after* startup, so the seeders never took
  effect until the next backend restart. All three seeders now also
  run from the `create_admin` bootstrap endpoint (idempotent).

### Built-in `bioaf-base` work-node environment (opt-in)

Resolves to-resolve.md issue #1 (backend portion). When
`BIOAF_BASE_WORK_NODE_IMAGE_URI` is set, the backend seeds a
system-managed `bioaf-base` work-node env whose single version is
`status=ready` with `image_uri` pre-populated -- so first-launch is
instant and users no longer have to wait through a ~10-15 min Packer
build before they can pick anything from the work-node environment
dropdown. If the env var is unset, the seed is a no-op and the
existing draft fallback still runs (backward-compatible).

The published Artifact Registry / GCE image itself is not yet built;
once available, set `BIOAF_BASE_WORK_NODE_IMAGE_URI` in the deploy
config to surface the seeded env.

### Build trigger UX

- The "build environment" trigger on `/environments` and
  `/pipelines/environments` no longer uses the bare browser
  `confirm()` dialog (which renders with the host IP in the title
  bar and leaks "Cloud Build" terminology users do not recognize).
  Replaced with the existing `ConfirmDialog` modal; copy now
  explains the build runs in the background and the user can keep
  using the app.
- The build trigger endpoint previously caught only `ValueError`,
  so any other failure (GCP API, credentials, packer step) bubbled
  up as a bare 500 with no JSON body and the frontend rendered a
  meaningless "Unknown error" alert. Now catches broad `Exception`,
  logs the stack server-side, and returns a 500 whose detail is the
  underlying error message so the user sees something actionable.

### Work-node Packer build no longer eats SSD quota

The Packer build VM (transient, runs once per work-node image
build) used `pd-ssd` for its 50 GB boot disk. That 50 GB counts
against the regional `SSD_TOTAL_GB` quota -- already pressured by
the GKE pool nodes' `pd-balanced` boot disks (which also count
toward SSD quota). On a fresh GCP project (default 250 GB
ceiling), one running pipeline pool plus the bioaf control VM
consume ~230 GB; the build's +50 GB tipped past 250 and failed
immediately with `Quota 'SSD_TOTAL_GB' exceeded`.

Switched to `pd-standard` for the build disk -- HDD vs SSD makes
no material difference for a one-off conda env install, and the
image artifact uploaded to GCE Image Service still works for
`pd-ssd` work-node boot disks at launch. A regression test
(`test_packer_template_disk.py`) guards `disk_type` and
`disk_size` so future drift trips a clear failure message.

This is a tactical mitigation; the proper fix (auto-request an
`SSD_TOTAL_GB` quota bump alongside the CPU bump) is tracked in
to-resolve.md issue #3.

## v0.11.1

Bug-fix point release for the v0.11.0 SA hardening work. Resolves the
issues found while testing v0.11.0 end-to-end on a fresh greenfield
install. No new features; no schema changes; no migration required.
Existing installs are unaffected (they continue to use the legacy
`service_account_key` code path).

### Sheets reader SA: now keyless

The v0.11.0 plan filed Breakage 6 ("Sheets reader needs `keys.create`,
which the org policy blocks") as a documented limitation. That
limitation was unnecessary -- Google's permission check is
identity-based, so a doc shared with `bioaf-reader@...` accepts an
impersonated token authenticating as that principal exactly the same
way it accepts a token from a stored JSON key.

- `install-gcp.sh` creates the `bioaf-reader` SA, enables
  `sheets.googleapis.com`, grants `bioaf-app`
  `roles/iam.serviceAccountTokenCreator` on the reader SA, and
  embeds the email in the prefill YAML.
- `get_reader_credentials` returns
  `impersonated_credentials.Credentials` in `vm_default` mode (signs
  via the IAM `SignBlob` API). Legacy
  `service_account_key` installs still use the stored JSON key.
- `create_reader_sa` (in-app fallback) drops `keys.create` entirely
  and writes a `tokenCreator` binding on the new SA.
- `sheets_reader_sa_key` is no longer written to `platform_config`
  on new installs.

Works on policy-enforced projects (`iam.disableServiceAccountKeyCreation`)
because that constraint only blocks `keys.create`, not
`serviceAccounts.create` or `serviceAccounts.getAccessToken`.

### K8s adapters and GCS clients routed through `credential_injector`

The original audit missed three adapters (notebook, compute, cellxgene)
and two GCS-client paths (upload signed URLs, storage stats) that had
their own `_get_gcp_credentials`/`_get_gcs_credentials` helpers calling
`json.loads` on `gcp_service_account_key`. Under SA hardening that row
is empty, so they raised `JSONDecodeError` on first use, blocking
notebook session launch and file upload, and 403'ing the storage stats
query.

- `adapters/{notebooks,compute,cellxgene}/kubernetes.py` now route
  through `credential_injector.load_gcp_credentials(cfg)`.
- `upload_service._get_gcs_credentials` and
  `gcs_storage.GcsStorageService.get_credentials` route through the
  same. v4 signed URLs work because impersonated credentials sign via
  the IAM `SignBlob` API and `tokenCreator` includes `signBlob`.
- `storage_service._query_gcs_buckets` /
  `get_lifecycle_policies` use impersonated bootstrap credentials
  (which have unconditioned `roles/storage.admin`) for project-level
  list operations.

### `gke_cluster_name = "null"` sentinel handling

`stack_deployment.py` writes the literal string `"null"` to
`platform_config` when a Terraform output is empty. The compute
adapter's GKE-metrics call forwarded `"null"` to the GKE API
verbatim and spammed Cloud Logging with `clusters/null`
PERMISSION_DENIED entries. New `_resolve_cfg` helper in
`compute/kubernetes.py` treats the sentinel as missing.
`_k8s_get_cluster_status` raises a clear error;
`_k8s_get_cluster_metrics` returns the safe-zero fallback.

### Pipeline launch reload of cluster_config

A backend that started before compute deploy completed could not
launch pipelines after deploy finished -- it failed with "No GKE
cluster endpoint in platform_config" because the sync K8s helpers
used by `ensure_pipeline_namespace` did not reload config from
`platform_config`. New `_ensure_cluster_config_fresh()` helper is
awaited from every async public entry point in the compute adapter
that uses sync K8s helpers downstream (`_k8s_submit_job`,
`_k8s_cancel_job`, `_k8s_get_job_status`, `_k8s_list_jobs`,
`_k8s_get_job_logs`, `_k8s_persist_job_logs`).

### `roles/monitoring.metricWriter` for the Ops Agent

Added to `bioaf-app`'s unconditioned project bindings in
`install-gcp.sh`, `installer/roles_manifest.yaml`, the backend
`APP_ROLES` fallback, and the frontend role-panel guidance. Same
low-risk profile as `roles/logging.logWriter` (write-only,
cost-only). Stops the Ops Agent's
`MonApiPermissionErr: missing roles/monitoring.metricWriter`
log spam.

### Tests

- 2086 backend tests pass (was 2067 in v0.11.0).
- New `test_k8s_adapter_sa_hardening.py` (6 tests) covers
  credential-injector routing across all three K8s adapters, the
  `"null"` sentinel, and the cluster-config reload guard.
- New `test_upload_credentials_sa_hardening.py` (5 tests) covers
  signed-URL credentials, storage-stats credentials, and graceful
  fallback when credentials are unavailable.
- Existing tests that patched the removed `_get_gcp_credentials`
  helper updated to patch `_load_gcp_credentials` instead.

### Known follow-ups (not in this release)

- Large-file upload UX: the v4 single-PUT signed-URL flow shows "0%"
  for several minutes on large files before progress starts moving.
  Root cause is browser-side `xhr.upload.onprogress` throttling on
  large request bodies. Fix is to switch to chunked resumable
  uploads (the protocol `resumableUpload.ts` already implements for
  reference data).
- Compute Terraform output capture: `gke_cluster_name = "null"` is
  written to `platform_config` even though `outputs.tf` declares
  the output. Read-side normalization stops the spammy errors but
  the upstream capture path needs investigation.
- Cellxgene Workload Identity: cellxgene pods can't authenticate to
  GCS under SA hardening because `_ensure_gcp_secret` writes an
  empty key into a K8s Secret. Proper fix is Workload Identity for
  the cellxgene SA.
- Quota-request UX: pipeline launches fail to schedule on
  CPU-quota-constrained projects with no useful in-app message. The
  pod sits Pending until the user manually requests a CPU quota
  increase in the Cloud Console. Captured for a future feature.

## v0.11.0

Service-account hardening for greenfield installs. Eliminates the JSON
service-account key, splits the broad single-SA into a scoped runtime SA
(`bioaf-app`, attached to the VM) and an impersonated bootstrap SA
(`bioaf-bootstrap`, used only for IAM/Terraform/Cloud Build), and bounds
the runtime SA's blast radius to bioAF-managed resources only via IAM
Conditions, Resource Manager tags, and per-resource bindings.

Existing installs are not migrated -- they keep their JSON-key code path
unchanged. The full design is in
`documentation/sa-hardening/03-consolidated-plan.md`.

### Architecture (greenfield only)

- New `bioaf-bootstrap` SA holds the broad project-level roles formerly
  given to the single SA, minus `roles/iam.serviceAccountKeyAdmin`.
  Impersonated by Terraform, Sheets reader provisioning, and the
  notebook/cellxgene/environment image-build services.
- New `bioaf-app` SA is attached to the GCE VM and holds a small set of
  scoped roles: `roles/storage.admin` (`bioaf-*` buckets only),
  `compute.instanceAdmin.v1` (`bioaf-*` VMs only), `container.admin`
  (resources tagged `bioaf-managed=true`), the project-scoped custom
  role `bioafSaManager`, plus `roles/iam.serviceAccountTokenCreator`
  resource-scoped to `bioaf-bootstrap` only.
- Project-scoped Resource Manager tag `bioaf-managed=true` attached to
  bioAF-managed GKE clusters; per-secret and per-subscription bindings
  for `bioaf-app` rendered by Terraform.
- New platform_config key `gcp_bootstrap_sa_email`, persisted at startup
  from VM instance metadata (`bioaf_bootstrap_sa_email`). The
  credential injector and image-build services prefer it over the
  legacy `gcp_service_account_email` so existing keyed installs keep
  working.

### Installer

- `install-gcp.sh` creates both SAs, the `bioaf-managed` tag, the
  custom IAM role, the conditioned bindings, and the resource-scoped
  tokenCreator binding. Attaches `bioaf-app` to the VM and writes the
  bootstrap email into VM metadata.
- The legacy "create SA + JSON key + paste worksheet" step is removed.
- New file: `installer/roles_manifest.yaml` -- single source of truth
  for both SAs' permissions, read by both the installer and the
  backend validation probe.

### Backend

- `credential_injector.load_gcp_credentials` reads `gcp_bootstrap_sa_email`
  first and falls back to the legacy email field.
- `notebook_image_service`, `cellxgene_image_service`, and (transitively)
  `environment_build_service` now obtain credentials via the injector
  so impersonation reaches Cloud Build / Artifact Registry.
- `terraform_executor` injects the bootstrap email into the env dict
  before `build_env`. The Terraform tfvars writer plumbs
  `bioaf_app_sa_email` and `bioaf_bootstrap_sa_email` through to the
  storage and compute modules.
- `gce.py` work-node adapter routes credentials through the injector
  (no more "no JSON key" hard error on greenfield) and drops the
  legacy `gcp_service_account_email` fallback for the VM-attached SA.
- `gcp_config.validate_gcp_credentials` runs a dual-SA probe in
  `vm_default` mode: bioaf-app via raw ADC, bioaf-bootstrap via
  impersonation. Merged result requires both. New
  `app_probe`/`bootstrap_probe` fields on `GCPValidationResult`.
- `sheets_reader_sa_service` surfaces a clear error when `keys.create`
  fails because the project enforces
  `iam.disableServiceAccountKeyCreation`.
- `main.py` lifespan reads `bioaf_bootstrap_sa_email` from VM metadata
  and persists it on first startup (idempotent; skipped outside GCE).

### Terraform

- Per-secret `roles/secretmanager.secretAccessor` bindings for
  bioaf-app (gated on `bioaf_app_sa_email`).
- Per-subscription `roles/pubsub.subscriber` bindings for bioaf-app on
  the ingest worker + dead-letter subscriptions.
- `bioaf-managed=true` tag binding attached to the GKE cluster in both
  the legacy top-level module and the backend `compute` module.

### Frontend

- Setup wizard and GCP settings page replace the hardcoded 14-role
  list with two adjacent panels: bioaf-bootstrap roles (broad) and
  bioaf-app roles (scoped). Validation result shows per-SA pass/fail
  cards when the new probe fields are present; falls back to the
  legacy single list for keyed installs.

### Tests

- `test_credential_injector` extended with three vm_default impersonation
  cases (new key, legacy fallback, neither set).
- `test_terraform_executor` extended to verify `_read_gcp_config`
  selects `gcp_bootstrap_sa_email` and `run_plan` passes it through.
- New: `test_sheets_reader_sa_service`,
  `test_image_build_credentials`, `test_gce_adapter`,
  `test_gcp_config_dual_probe`, `test_bootstrap_metadata`,
  `test_roles_manifest`.
- New CI invariants: `test_bucket_naming_invariant` (every
  `google_storage_bucket` starts with `bioaf-`),
  `test_compute_naming_invariant` (every Python `instance_name = '...'`
  starts with `bioaf-`), `test_gke_tag_invariant` (every
  `google_container_cluster` file declares a sibling
  `google_tags_tag_binding`).

### Documented limitation

- Sheets integration cannot be enabled on projects that enforce
  `iam.disableServiceAccountKeyCreation` because the Sheets reader SA
  still requires `keys.create`. The setup wizard now surfaces a clear
  message on enable rather than a stack trace.

## v0.10.3

Reference Data Ingest: completes the four user-facing capabilities of ADR-017 / ADR-047 (upload, import-from-URL, versioning, and pipeline linkage). Existing reference data CRUD is unchanged; this release adds everything around getting bytes into the registry and using them in pipelines.

### New features

- **Reference upload** -- drag-drop multi-file upload page at `/data/references/new`. Bytes go directly to GCS via resumable session URLs (8 MiB chunks; 64 MiB for files > 1 GiB) so 30+ GB CellRanger references survive flaky lab Wi-Fi
- **Import from URL** -- separate page at `/data/references/import` for pulling references from public sources (GENCODE, 10x, etc.). A per-import GKE Job streams the source into GCS, supports `none`/`gzip`/`tar`/`tar.gz` extraction modes, and reports progress via a polling endpoint
- **Versioning UX** -- reference detail page gets a Versions tab listing every `(name, category)` sibling, with the current row highlighted and deprecated rows dimmed. New "Upload new version" button pre-fills name + category + scope and locks them so only version + files differ
- **Reference parameter type for custom pipelines** -- custom pipelines can declare a parameter as `variable_type='reference'` with a `reference_category` (`genome`/`annotation`/`index`/`atlas`/`markers`/`other`/`any`). At launch, those parameters render a searchable dropdown of active references in that category; the selected dataset's path is stored in run parameters so the existing auto-linker picks it up
- **Linked references on run detail** -- the "References Used" table on `/pipelines/runs/[id]` adds a Category column and turns each reference name into a link back to its detail page

### New endpoints

- `POST /api/references/upload-init` -- create a reference in `status='uploading'` and return per-file GCS resumable session URLs
- `POST /api/references/{id}/upload-complete` -- list the GCS prefix, verify every declared file arrived, persist md5 + size, flip status (`internal -> active`, `public -> pending_approval`, mismatch -> `failed` with prefix purge)
- `POST /api/references/{id}/abort` -- purge GCS objects and delete the reference row (idempotent)
- `POST /api/references/import` -- launch the importer GKE Job
- `GET /api/references/{id}/import-status` -- read progress (`pending`/`downloading`/`verifying`/`extracting`/`finalizing`/`active`/`failed`)
- `POST /api/references/{id}/import-cancel` -- terminate the GKE Job and abort the reference
- `GET /api/references/by-name?name=...&category=...` -- return every version for a `(name, category)` tuple in one round-trip
- `POST /api/internal/references/{id}/import-progress` -- importer-container callback authenticated by `X-Internal-Token` (settings.internal_token); the auth middleware exempts `/api/internal/*` so the container can reach it without a user JWT

### Roles & permissions

- New `references` resource with `view` and `upload` actions. Migration 071 backfills both for `admin`/`comp_bio` and `view` for `bench`/`viewer` on existing system roles. Existing endpoints unchanged; new endpoints use `references:upload`

### Database (additive only)

- Migration 071: backfill `references:view`/`upload` permissions
- Migration 072: `reference_import_progress` table tracking GKE-job-driven imports (PK `reference_id`, cascade delete)
- Migration 073: `custom_pipeline_variables.reference_category` column
- `REFERENCE_STATUSES` extended with `uploading` and `failed`

### Infrastructure

- Terraform `storage` module gains a `bioaf-references-{org_slug}-{stack_uid}` bucket with versioning + CORS for browser PUT/POST
- New platform_config key `references_bucket_name`, populated by the storage stack on apply
- New env var `BIOAF_INTERNAL_TOKEN` for the importer-callback secret

### Spec

- `documentation/spec-reference-data-ingest.md` is the source of truth for this release

## v0.10.2

Point release adding per-pipeline QC dashboard configuration. Existing scRNA-seq dashboards render identically; new templates plug in by shipping a config + extractor instead of forking the dashboard page.

### New features

- **Per-pipeline QC templates** -- pipelines now declare a `qc_template` (`scrnaseq`, `bulk_rnaseq`, or `custom`) and may carry a `qc_config_json` override. The QC dashboard reads sections, metric labels, formats, thresholds, and chart specs from that config instead of hardcoded scRNA-seq logic
- **Custom-pipeline QC dashboards** -- custom pipelines that emit `/outputs/qc_metrics.json` get a real QC dashboard rendered from the version's `qc_config_json`. Both fields are versioned with the pipeline (per ADR-033 immutability) so editing the layout produces a new version
- **QC dashboard config in the pipeline editor** -- new collapsible "QC dashboard config" panel on the custom-pipeline version form: template select + JSON textarea with client-side parse + object-shape validation
- **Generic QC dashboard renderer** -- the QC dashboards page replaces its hardcoded scRNA-seq body with a config-driven `<GenericQCDashboard/>`. Sections, metric cards, formats, threshold colors, and charts all dispatch off `qc_config`
- **Reproducibility snapshot** -- each generated dashboard row stores the resolved render config, so old runs always render the way they were generated even after a pipeline's config changes later

### New documentation

- **`docs/guides/custom-pipelines.md`** -- end-to-end guide to authoring custom pipelines: prerequisites, runtime contract, variables, version cascade, permissions
- **`docs/guides/custom-qc-config.md`** -- reference for the QC config schema (sections, metrics, formats, thresholds), how to emit `qc_metrics.json`, and a hello-world example

### Backend

- New columns on `pipeline_catalog`, `custom_pipeline_versions`, and `qc_dashboards` (additive migration 070) for `qc_template` + `qc_config_json`
- New `app/services/qc/` package: per-template extractors + render configs (`scrnaseq`, `bulk_rnaseq`, `custom`), a resolver that walks run -> custom-pipeline-version -> catalog-entry -> default fallback, and a shared GCS helpers module
- `QCDashboardService` is now a thin orchestrator that dispatches via the template registry; existing `_read_*` helpers remain on the class as backwards-compat shims
- `QCDashboardResponse` gains `qc_config` and `raw_metrics` fields. Pre-snapshot rows substitute the resolved template default on read so legacy dashboards still render
- `CustomPipelineVersionCreateRequest` + `Response` carry `qc_template` + `qc_config_json`; Pydantic enforces the JSON-object shape

## v0.10.1

Point release tightening file upload, association, and provenance display.

### Fixes

- **Drag-and-drop uploader accepts any file type** -- the FASTQ/h5ad/CSV/TSV allowlist silently dropped legitimate files (protocols, READMEs, analysis exports)
- **File search inherits through samples** -- searching by experiment now returns files attached to that experiment OR to any of its samples; searching by project pulls in files at every level beneath. Sample-level pipeline outputs no longer hide from the experiment view.
- **File tiles show full provenance** -- each row renders a `Project > Experiment > Sample > Pipeline Run #N` (or `... > Notebook` / `... > Work Node`) breadcrumb under the filename, with the resolved creator (uploader or pipeline/session launcher) in its own column
- **Explicit Global scope on upload** -- the upload page now requires picking Global / Project / Experiment / Sample; Global files render a distinct badge so they are not confused with truly unassociated files
- **Files page matches Experiment > Files** -- the Data & Files > Files page now uses the same FileBrowser layout as the experiment-scoped Files tab
- **Documents page removed from Data & Files menu** -- file handling is consolidated under Files

### Backend

- New `is_global` column on `files` (additive migration 069)
- `FileService.list_files` cascades inheritance through `sample_files` for both experiment and project filters
- `FileService.get_provenance_for_files` returns batched project/experiment/sample/pipeline-run/compute-session/creator data per page

## v0.10.0

Custom pipelines: define and run user-authored pipelines (bash, Python, Perl, R, etc.) against tracked input data with full provenance, versioned definitions, and Conda-based environments.

### New features

- **Custom Pipelines** -- author pipelines in any language by combining a script, command, and pipeline environment; runs execute as K8s Jobs with input mounts, output collection, and report detection (ADR-044)
- **Pipeline Environments** -- new "Pipeline" environment type with conda-only Docker build routing, separate from Notebook and Work Node environments; managed from a new Pipelines > Environments page (ADR-045)
- **Versioned pipeline definitions** -- each save creates a new pipeline version with its own script, command, variables, and pinned environment version; runs always reference the version they launched against
- **Pipeline variables** -- declare typed variables (string, number, file, sample) on a pipeline; values are validated and delivered as environment variables and a `params.json` manifest at runtime
- **Version cascade** -- rebuilding a pipeline environment automatically creates new minor versions of any pipelines that pin it, via an event-bus-driven cascade handler (ADR-046)
- **Custom pipeline catalog integration** -- the pipeline catalog now lists custom pipelines alongside nf-core entries, with creator and latest-version metadata surfaced on each card
- **Custom pipeline launch dialog** -- type-aware launch flow that renders variable inputs (including file/sample pickers) and submits to the custom-pipeline endpoint
- **Run detail for custom pipelines** -- run detail page renders the pipeline-supplied report (HTML or markdown) and the captured log file, with project/experiment links pulled from launch context
- **Project-scoped outputs** -- custom pipeline outputs register against the launching project (and experiment when applicable) with `pipeline_output` source type and full provenance back to the pipeline version

### Backend

- New models: `CustomPipeline`, `CustomPipelineVersion`, `CustomPipelineVariable`; pipeline_runs gains `custom_pipeline_version_id` and `output_files_json` columns (migration 068)
- `CustomPipelineService` covers CRUD, version management, launch orchestration, manifest building, and output sync
- Kubernetes compute adapter learns to launch custom-pipeline jobs with conda activation, input staging, output collection to GCS, and report artifact detection
- Pipeline monitor handles custom-pipeline run lifecycle: status transitions, log/report retrieval, and output registration via `_handle_completion`
- New API router `app/api/custom_pipelines.py` with permissions `custom_pipelines:create|read|update|delete|launch`, seeded into the four built-in roles

### Frontend

- New pages: Pipelines > Custom (list), Pipelines > Custom > [id] (detail with versions, variables, runs), Pipelines > Environments
- `CustomPipelineLaunchDialog` reuses `FileTreeSelector` for file/sample variable inputs
- Run detail page renders the report and log produced by the pipeline; navigation gains a Pipelines > Environments entry

## v0.9.0

Work Nodes overhaul: GCE VMs with conda environments, GitHub repo cloning, and a redesigned file picker.

### New features

- **Work Nodes on GCE VMs** -- work nodes now launch as full Linux VMs on Google Compute Engine instead of GKE Pods, with SSH access via session credentials (ADR-043)
- **Packer-built VM images** -- work node environments build as GCE VM images via Cloud Build + Packer with conda environments pre-installed for fast startup
- **Independent environment types** -- environments are now tagged as "Notebook" or "Work Node" with separate image pipelines; work node environments are conda-only
- **GitHub repo cloning** -- users manage a list of GitHub repos on the Work Nodes page; selected repos are automatically cloned into `~/repos/` when a work node boots
- **MOTD** -- work nodes display a message of the day on SSH login showing paths to input data, repos, outputs, and scratch space
- **File picker for work nodes** -- the launch wizard now uses the same FileTreeSelector as notebooks, with project -> experiment -> file selection and sample grouping
- **Default Work Node environment** -- a base conda environment (Python 3.11, numpy, pandas, scipy, matplotlib, etc.) is automatically seeded on first boot
- **Files page search and filters** -- added filename search, project filter, experiment filter, and source type filter to the Data & Files page
- **Work node output tracking** -- outputs from work nodes are registered as `work_node_output` source type, distinct from notebook outputs

### Improvements

- **Zone retry on capacity exhaustion** -- VM creation tries zones b, c, f, a in order if GCE returns ZONE_RESOURCE_POOL_EXHAUSTED
- **E2 machine types** -- added e2-standard-4, e2-standard-8, and e2-highmem-8 with better availability than N2 in constrained regions
- **Resource failure UX** -- failed launches due to GCP capacity show "Resource Failure" status with a "GCP Resources Unavailable" detail banner instead of generic "failed"
- **Stop persistence** -- stopping a work node immediately commits a "stopping" status so navigating away no longer shows stale "running" state
- **Project file filter** -- the project filter on the Files page now includes files associated via experiment, not just direct project_id

### Bug fixes

- Fix Packer template syntax (double braces, missing packer init, universe repo, miniconda TOS)
- Fix SSH access (password auth via sshd drop-in config, username in SSH command)
- Fix output sync (SSH into VM before stopping instead of unreliable shutdown hooks)
- Fix environment rebuild for work node type (was hardcoded to Dockerfile format)
- Add google-cloud-compute dependency for GCE adapter

## v0.8.3

Fixes fresh install failure and improves the GCP installer experience.

### Bug fixes

- Fix setup failing on fresh VMs with "vunknown" image tag by adding a grep/sed fallback for Python < 3.11 (Ubuntu 22.04 ships Python 3.10 without tomllib)
- Setup now queries GitHub releases for the latest version with available images, walks back through recent releases if needed, and falls back to building from source as a last resort

### New features

- Add `--version` flag to `./bioaf setup` for installing a specific version (e.g., `./bioaf setup --version 0.8.1`)
- Add a Setup Worksheet section to the GCP installer output with the project ID, region, and service account JSON key highlighted in green for easy copy-paste

## v0.8.2

Update flow improvements for faster updates with less downtime.

### Bug fixes

- Pull pre-built images before restarting containers so the app stays online during the download, reducing downtime to just the restart + migrate window
- Move Cloud Logging agent setup to after restart so it does not block the update while the app is down
- Show restart countdown message in the CLI so users know why the update pauses before restarting

## v0.8.1

Pre-built Docker images published to GitHub Container Registry on each release. This is the first version to ship remote artifacts -- setup and updates now pull pre-built images instead of building on the VM, reducing install and update time from 15+ minutes to seconds. Users who encounter issues can install from source using v0.8.0 and below as they have to this point.

### New features

- Publish backend, frontend, and cellxgene Docker images to ghcr.io on each release, with GHA build cache for fast CI builds
- Setup and update commands pull pre-built images from ghcr.io instead of building locally on the VM
- Frontend image build validation added to PR CI pipeline

### Platform updates

- Add OCI source labels to Dockerfiles for automatic ghcr.io repository linking
- Add `image:` directives to docker-compose.yml alongside `build:` directives, supporting both pull (production) and build (development) flows
- Move Cloud Logging agent setup from start to setup/update only, eliminating a 5+ minute apt-get delay on every restart

## v0.8.0

Google Sheets integration for experiment field setup and sample import.

### New features

- Import column headers from a Google Sheet during experiment creation to populate sample field defaults and custom fields, with a column mapping UI that lets users map sheet columns to existing fields or create new custom fields
- Import sample data directly from a Google Sheet using the same preview, column mapping, and confirm flow as CSV import
- Dedicated reader service account provisioned automatically via the IAM API, managed from Settings > Integrations > GCP
- Auto-match unknown columns to existing custom fields when names match, so repeat imports pre-select the right mapping
- All 19 user-facing sample fields are now recognized during import (not just the 10 defaultable ones), with visual indicators distinguishing fields that support defaults from per-sample fields

### Platform updates

- Add `iam.serviceAccountKeys.create` permission and `roles/iam.serviceAccountKeyAdmin` role to the GCP setup checklist, install script, settings UI, and setup wizard
- Add `google-api-python-client` dependency for Sheets API v4 and IAM Admin API access

### Bug fixes

- Fix IAM propagation race condition when creating the reader service account key immediately after SA creation
- Fix dropdown collision where "Add as new custom field" and "Map to existing custom field" had identical select values when the column name matched an existing field name
- Distinguish "Sheets API not enabled" from "sheet not shared" errors so users get actionable guidance

## v0.7.5

Plot Archive thumbnail bug fix.

### Bug fixes

- Restore Plot Archive previews that broke after the content-token JWT rework. Thumbnails rendered an empty `<img src="">` while the content token was still being fetched, which fired `onError` and latched the card into the "No preview available" fallback. The grid now shows a skeleton until the short-lived content URL is ready.

## v0.7.4

In-app update UX improvements.

### Platform updates

- Add a 60-second "restart warning" step between build and restart, giving users a visible countdown before the backend briefly goes offline during an update
- Settings > Platform Info re-attaches to an in-progress update on page mount, so navigating away and back shows live status instead of an empty banner
- Countdown duration is configurable via BIOAF_RESTART_WARN_SECONDS and skippable with BIOAF_SKIP_RESTART_WARN=1 for development

## v0.7.3

OOM detection, preemption classification, and cluster configuration UX improvements.

### Pipeline monitoring

- Detect OOM-killed pipelines from K8s container termination reasons and set failure_reason to "oom" with actionable guidance
- Detect Spot preemption exhaustion from failed process exit codes (143/137/247) and set failure_reason to "preemption_exhausted"
- Emit PIPELINE_OOM event (critical severity) for notification routing
- Store exit_code on PipelineProcess records from adapter progress data

### Infrastructure UI

- Reorganize cluster config panel into "Pipeline Nodes" and "Interactive Nodes" columns
- Add Spot instance info tooltip explaining cost savings and auto-retry behavior
- Remove pt-5 alignment hack on the Spot toggle

### Pipeline run UI

- Show amber OOM banner with "Update node size" link on run detail page
- Show blue preemption banner with "Re-run" and "Disable Spot" actions on run detail page
- Add orange "OOM" and blue "Preempted" badges on the pipeline runs table

### Database

- Add nullable failure_reason column to pipeline_runs (migration 066)

## v0.7.2

Security hardening from external pentest, deployment and setup reliability fixes.

### Security

- Reject known-insecure JWT secret keys at startup
- Disable OpenAPI docs and Swagger UI in production
- Remove smtp_configured from unauthenticated bootstrap status response
- Require authentication for /api/health/services and /api/health/status
- Replace session JWTs in file/plot content URLs with short-lived scoped tokens (60s TTL)

### Deployment

- Fix GCP zone fallback for regions without a "-a" zone (us-east1, europe-west1)
- Reduce GKE default node pool disk to 30GB to stay within default SSD quota
- Zone fallback in install-gcp.sh retries all zones before failing
- Unique service account name per install to avoid stale IAM bindings
- Role grant errors are now reported instead of silently swallowed
- User-friendly error message for GCP quota failures during deploy
- Add missing google-cloud-iam dependency for orphaned resource cleanup

### Setup Wizard

- Block advancement when GCP validation fails, show results inline
- Surface terraform error details instead of generic "Apply failed"
- Log terraform init exceptions with full traceback

### Fixes

- Fix filename collision when uploading multiple files to same sample
- Fix React hooks violation in PlotThumbnail component
- Run history table shows error messages for failed operations

## v0.7.1

Fix validation error display in setup wizard and all API error surfaces.

### Fixes

- Pydantic 422 validation errors (e.g. invalid org slug) now display the actual error message instead of "[object Object]"
- Applies to all API calls: fetchApi, uploadFile, and downloadFile

## v0.7.0

Security hardening and deployment reliability improvements from external pentest findings.

### Security

- Reject known-insecure JWT secret keys at startup (prevents running with default/public secrets)
- Disable OpenAPI docs and Swagger UI in production (404 instead of serving 376-endpoint schema)
- Remove smtp_configured from unauthenticated bootstrap status response
- Require authentication for /api/health/services and /api/health/status endpoints
- Replace full session JWTs in file/plot content URLs with short-lived, resource-scoped tokens (60s TTL)
- New POST /api/content-tokens endpoint for issuing scoped content access tokens

### Deployment Fixes

- Fix GCP zone fallback for regions without a "-a" zone (us-east1, europe-west1)
- Add backend region-to-zone mapping with correct zones for all 17 supported regions
- Setup wizard now blocks on GCP validation failure and displays missing permissions inline
- Fix zone fallback in frontend settings and setup wizard

## v0.6.6

Automated backup scheduling and timezone fixes across the platform.

### Backup Scheduling

- Backups now run automatically on a user-configured schedule instead of requiring manual triggers
- Enable/disable toggle per tier (PostgreSQL and platform config)
- Set first backup to "now" or a specific future date/time
- Configurable cadence (hours between backups) and retention (days to keep)
- Background loops poll every 60 seconds and execute when a backup is due
- Backups older than the retention period are automatically deleted from GCS
- Add config backup background loop (was previously missing)

### Fixes

- Fix backup settings not persisting across page refreshes (missing transaction commit)
- Fix scheduled backup time shifted by timezone offset (naive local datetime treated as UTC)
- Standardize all date/time display to user's local timezone across the platform

## v0.6.5

Installability improvements: one-command GCP setup, versioned updates from CLI and UI.

### GCP Installer

- Add `install-gcp.sh` for one-command GCP provisioning (VM, firewall, service account)
- Script installs gcloud CLI if needed, creates an e2-medium VM with Docker pre-installed
- Waits for SSH and Docker readiness before presenting next steps
- Optionally creates a service account and prints the JSON key for the setup wizard

### Update System

- `./bioaf update` now accepts an optional version argument (e.g., `./bioaf update 0.7.0`)
- Backs up the database before every update
- Fetches and checks out the target git tag instead of following a branch
- Writes progress status for the UI to track
- Add host-side update agent (systemd service) that watches for trigger files from the backend
- Add "Install Update" button on Settings > Information page with real-time progress display
- Backend resolves pending upgrades on startup (marks completed or failed)

### Fixes

- Fix `get_access_url` on GCE to query metadata server for external IP instead of showing internal 10.x address
- Auto-activate docker group via `sg docker` instead of requiring re-login after VM setup
- Fix bash 3.2 compatibility in installer (macOS ships bash 3.2)

### Housekeeping

- Remove orphaned `frontend/src/app/admin/` pages (all had permanent redirects)
- Update deployment guide, ADR-005, README, and user guides for accuracy

## v0.6.4

Plot archive bug fixes, PDF thumbnail generation, and detail modal improvements.

### Bug Fixes (Issue #151)

- Add unique constraint on `platform_config.key` to prevent duplicate rows that broke the plot archive scanner
- Fix scanner using bare ADC instead of app service account credentials
- Add SVG (`image/svg+xml`) and PDF (`application/pdf`) content-type mappings to file content endpoint
- Preserve click handler on plot thumbnails that fail to load

### PDF Thumbnails

- Render PDF page 1 to PNG thumbnails using PyMuPDF, stored under `_thumbnails/` prefix in the results bucket
- Scanner auto-generates thumbnails when indexing new PDF plots
- Add `GET /api/plots/{id}/thumbnail/content` endpoint for serving thumbnail bytes
- Extend `POST /api/plots/backfill` to generate missing thumbnails for existing PDFs
- Clean up thumbnail blobs from GCS when associated files are deleted
- Offload PDF rendering to thread pool to avoid blocking the event loop

### Plot Detail Modal

- Rework modal to match Data & Files detail layout with metadata grid and download button
- Display project, experiment, pipeline, session, source, and indexed date
- Add file format badge (PNG, SVG, PDF) to thumbnail cards
- Standardize card title font sizing

## v0.6.3

Infrastructure lifecycle stability, Cloud Logging, and deploy UX improvements.

### Cloud Logging

- Auto-detect GCE and attach Cloud Logging using the app's configured service account
- Install Ops Agent via `./bioaf start` for Docker container log collection
- Add `logging.logEntries.create` to GCP validation permission checks

### Infrastructure Lifecycle

- Replace 30-minute hard timeout with in-memory process registry so GKE deploys run to completion
- Fix lock file deletion to use app credentials instead of ADC
- Fix orphaned resource cleanup returning 404 for valid resources
- Expand orphan detection and cleanup to cover IAM service accounts
- Deduplicate orphaned resource entries across repeated failures
- Add GKE cluster and service account scanning via GKE/IAM APIs
- Persist tfvars on each TerraformRun for audit and reproducibility (migration 064)

### Deploy UX

- Show full planned resource list in deploy modal from the start (Queued/Setting up/Done states)
- Move teardown and storage destroy to background endpoints with polling
- Add region/zone selection at deploy time with cross-region cost warning
- Fix empty modal when no active run (idle state with "Starting operation...")
- Fix modal stuck after operation completes (terminal status persistence)
- Visible scrollbar on resource list

## v0.6.2

Audit log coverage gaps and activity feed event fixes.

### Audit Log Coverage (closes #153)

- Add logout endpoint (POST /api/auth/logout) with audit logging
- Log failed login attempts with reason (invalid credentials, account deactivated)
- Log file content serving as download audit entries
- Change role update audit action from generic "update" to "role_change" with old/new role names
- Log environment build success and failure from the build poller
- Log postgres and config backup completion and failure
- Log quota exceeded events alongside event bus emission
- Log notebook session access (who opened which session)
- Normalize download action name from "downloaded" to "download"
- Update audit log page filters with new entity types and actions
- Color-code new action badges (failures red, success green, warnings amber, role changes purple)

### Activity Feed Fixes

- Add PIPELINE_STARTED event type, emitted on successful run submission
- Emit PIPELINE_COMPLETED and PIPELINE_FAILED from pipeline monitor completion handler (event types existed but were never fired)
- Fix AUTO_RUN_BUDGET_DISABLED payload using wrong key ("organization_id" instead of "org_id"), silently dropped by NotificationRouter
- Fix AUTO_RUN_LAUNCHED payload missing org_id, user_id, and all display fields
- Frontend logout now calls backend endpoint so audit log entry is created

## v0.6.1

Pipeline run cost estimates based on actual GCP instance pricing.

### Cost Estimates

- Store cost estimate from compute adapter when launching a pipeline run (closes #203)
- Replace flat-fee stub with actual hourly spot rate for the pipeline node pool (n2-highmem-16)
- UI column renamed from "Cost" to "Est. $/hr" to clarify that values are hourly node rates, not totals

## v0.6.0

Automatic pipeline runs triggered by sample completeness, manifest reconciliation fixes, pipeline execution fixes, and UI cleanup.

### Auto-Run Pipelines

- Configure pipelines to run automatically when all expected files for a sample arrive and pass MD5 verification
- New ExperimentAutoRun and PendingAutoRun models with API endpoints for CRUD and status
- Background loop launches pending runs after configurable delay
- Auto-run evaluation integrated into the manifest ingest flow
- Replaced old trigger infrastructure (trigger_service, pipeline_triggers) with the new auto-run system

### Manifest Ingest Fixes

- Fix race condition where files arriving before the manifest were never linked to samples
- Retroactive reconciliation: when a manifest arrives, match already-ingested files by MD5 + filename + org + 2-hour time window
- Content-aware redelivery guard: compare incoming manifest entries against existing ones instead of just checking for existence
- Forward-path query now prefers MD5+filename match, falls back to filename-only for checksum mismatch detection
- Shared reconcile_manifest_entry() helper eliminates duplication between forward and retroactive paths

### Pipeline Execution Fixes

- Re-enable Fusion for GCS-backed pipeline runs (was incorrectly made opt-in, breaking all K8s process pods)
- Fix trace parser reading wrong column for process names ("process" vs "name" in Nextflow trace.tsv)
- Fix Nextflow K8s executor test to match Fusion-always-on behavior

### Pipeline Run UI

- Show pipeline logs directly without process dropdown for K8s runs (single log, no selection needed)
- Auto-detect protocol from sample chemistry_version, remove manual CV dropdowns from launch wizard
- Add bulk sample deletion with confirmation modal

### Navigation and Settings

- Remove unused Pipeline Scheduling placeholder page
- Move Naming Profiles from Settings to Data & Files section
- Consolidate GCP, SMTP, and Slack settings into Settings > Integrations with tabbed layout
- Add Seqera tab with coming-soon placeholder for Fusion license support

## v0.5.5

Auto-ingest pipeline hardening and manifest-driven file association. Groundwork for the upcoming auto-run pipeline feature.

### Auto-Ingest Fixes

- Pass stored GCP service account credentials through all downstream GCS operations (manifest reads, file copies, cleanup deletes)
- Fix double-delete: skip cleanup when move_file already deletes the source
- Fix duplicate manifest entries on Pub/Sub message redelivery
- Fix ManifestEntry reconciliation when duplicate pending entries exist
- Convert base64 MD5 from GCS Pub/Sub to hex for manifest checksum comparison
- Move manifest reconciliation before file copy so resolved experiment IDs determine the GCS prefix

### Manifest-Driven Sample Linkage

- Derive experiment and project from resolved samples in manifest ingest
- Create sample_files junction rows during file ingest for manifest-resolved samples
- Set file.experiment_id from manifest resolution so files appear in the correct experiment
- Add batch-position mapping via sample_index (S-number) segment in naming profiles

### UI

- Replace Sample Batch, Seq. Batch, and Pos. columns on the samples table with a Files count column
- Fix CSV upload custom field storage and mapping
- Fix auto-ingest settings save and listener restart behavior

### Housekeeping

- Rename sample_id_external to sample_id_unique across the codebase (DB column unchanged, additive-only)
- Fix file deletion blocked by manifest_entries FK constraint
- Fix serialize_entity to handle attribute/column name mismatches

## v0.5.4

Bug fix for database restore and UI cleanup on the Backup & Recovery page.

- Fix `_build_restore_url()` mangling database credentials when the PostgreSQL username contains "bioaf" (caused auth failures after restore swap)
- Replace browser `confirm()` dialogs with in-app ConfirmDialog on all backup restore/accept/reject actions

## v0.5.3

Setup wizard overhaul and installer improvements.

### Setup Wizard

- Setup flow now starts with a terminal-issued setup code that proves host access, replacing the old email verification step
- Wizard steps reordered: setup code, admin creation, org name, GCP credentials, SMTP, infrastructure decision, stack selection
- "Skip for now" buttons renamed to "Do this later" throughout
- Infrastructure step is a decision fork: deploy now or configure later
- Infrastructure init button shows processing state during terraform setup
- Removed team invite step from the wizard (available later from Settings)
- Price estimate removed from Kubernetes + GCS card

### CLI

- `./bioaf setup` now auto-runs the installer when `.env` or TLS certs are missing, so users can go from `git clone` to `./bioaf setup` in one step
- `./bioaf setup` prints the one-time setup code in green with the login URL
- macOS and Windows are detected early with a message pointing to the GCP setup docs
- `./bioaf create-admin` deprecated in favor of the web-based setup wizard

### Backend

- New `SetupCodeService` generates 6-character alphanumeric codes (bcrypt hashed, 1-hour TTL, single-use)
- New bootstrap endpoints: `generate-setup-code` and `verify-setup-code`
- `create-admin` endpoint now requires a setup JWT instead of being fully open
- Bootstrap status endpoint returns `has_setup_code` and `has_admin` fields
- Non-streaming `POST /api/v1/infrastructure/terraform/init` endpoint for the setup wizard
- Migration 061 adds `setup_code_hash` and `setup_code_expires_at` to organizations

### Getting Started (stubbed)

- 13-slide onboarding component with highlight overlays built but not yet linked
- Screenshots from marketing site included as placeholders, will be recaptured from the running app
- Route and component exist at `/getting-started` but are not accessible from the UI

## v0.5.2

Batch UX rework, custom fields, and entity snapshots.

### Batch UX

- Batches are now text fields on samples with find-or-create behavior instead of separate management pages with ID assignment
- Sample batches scoped per experiment, sequencing batches scoped per organization
- Batch codes added to sample field defaults at experiment creation
- CSV upload columns renamed to user-facing `sample_batch` and `sequencing_batch`
- Batches tab renamed from "Sample Batches" and counter removed

### Custom Fields

- Custom fields section on experiment create always visible (no longer gated behind template selection)
- Template-driven custom fields auto-populate; users can add arbitrary fields on top
- Custom fields support `is_required` flag with migration 059
- Custom fields editable on experiment detail page overview
- Experiment custom fields now inherited by samples as per-sample values (migration 060, new `sample_custom_fields` table)
- Sample create/edit forms render experiment custom field inputs
- Sample view modal displays custom field values

### Entity Snapshots

- Entity snapshots model and migration for point-in-time metadata capture
- Snapshot integration into audit service with optional snapshot parameter

### Manifest-Driven Ingest (foundation)

- Sequencing batch and manifest entry models with API
- Manifest parsing service for md5sum and CSV formats
- Manifest retry service for pending file verification
- Activity feed logging for manifest ingest events
- Sample completeness trigger and trigger_on schema field
- Auto-ingest manifest configuration UI
- This lays the groundwork for pipeline automation but does not finalize it

### Other

- Restored dropped columns on `sample_batches` (instrument model, platform, quality score encoding, sequencer run ID)
- GEO export reads instrument from sequencing batch
- Dropdown widths in field defaults now match text input widths

## v0.5.1

Improve notebook file selection UX.

- Files in the notebook launch picker are now sub-grouped by GCS subdirectory path (e.g., star/001/Gene/filtered vs star/001/Gene/raw), so identically named files from different pipeline stages are clearly distinguishable
- Each file shows a source type badge (Pipeline, Notebook, Upload) and creation date
- Files linked to a sample no longer duplicate under "Experiment Files"
- Launch and detail modals widened to 800px to prevent truncation

## v0.5.0

Notebook file lifecycle and environment build versioning.
This release introduces a complete file lifecycle for notebook and SSH sessions, fixing GCS storage mounting and adding structured input/output management with full provenance tracking.

### GCS Storage Fixes

- Fix GCS bucket mounting for notebook and SSH sessions: working bucket config, FUSE CSI annotation, SA key secret mount, and gcloud auth activation for Workload Identity environments
- Fix Workload Identity annotation not applied after namespace was cached
- Add gcs-sync sidecar container for reliable output persistence at shutdown

### Notebook File Lifecycle (ADR-040)

- Input files now mount with directory structure preserved: `/data/{project}/{experiment}/{sample}/{tool}/filename`
- Designated `/outputs/` directory on all session types (Jupyter, RStudio, SSH) for persistent analysis outputs
- On shutdown: outputs synced to GCS, notebook/script files (.ipynb, .Rmd, .R, .py) captured automatically
- Two-phase output persistence: working bucket during session, moved to results bucket on close
- Output files registered with full provenance (source_type=notebook_output, linked to project/experiment)
- 30-minute shutdown timeout for large file sync with UI status indicator
- Fix FILE_INVENTORY.md shell escaping that broke init container file copying partway through

### Environment Build Versioning (ADR-041)

- Rebuilding an environment version creates a minor version (v1 rebuild produces v1.1) instead of overwriting the image
- New `build_number` column on EnvironmentVersion with unique constraint
- Image tags use `v{version}.{build}` format (e.g., `v1.1`, `v1.2`)
- New rebuild endpoint: `POST /environments/{id}/versions/{vid}/rebuild`
- Notebook sessions now link to `environment_version_id` for traceability

### Provenance

- Session provenance endpoint: `GET /notebooks/sessions/{id}/provenance`
- Provenance reports for notebook outputs now include environment version, input files, session resources, and git info
- Markdown and PDF renderers display full source section for notebook and pipeline outputs
- Provenance preview panel displays inline source details instead of skipping nested data

### Frontend

- Shutdown sync indicator: spinner with "Syncing outputs to GCS..." while session stops
- Environment version picker shows `v{version}.{build}` format and passes `environment_version_id` in launch request
- Session detail modal shows environment version and provenance for stopped sessions
- Toggleable quick start guide on Notebooks and Work Nodes pages explaining `/data/`, `/outputs/`, environments, git, and credentials

### Schema Changes

- Migration 057: adds `build_number` to `environment_versions`, `gcs_output_prefix` to `compute_sessions`

## v0.4.1

Fix cellxgene adapter, image pipeline, and publish UX (#195)

## v0.4.0

Usability: real backups, service health, version checking (#194)
