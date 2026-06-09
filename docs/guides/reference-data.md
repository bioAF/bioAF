# Reference Data Management

bioAF provides a managed Reference Dataset layer for genome sequences, gene annotations, genome indices, reference cell atlases, marker gene lists, and gene ontology databases. This guide covers uploading reference data, managing versions, deprecating outdated references, and assessing the impact of changes.

## Why Managed Reference Data Matters

Every computational biology analysis depends on external reference data. Without centralized management, teams encounter two common problems:

- Someone updates a reference file and silently breaks reproducibility for every pipeline that used the old version.
- Different users maintain their own copies, leading to inconsistent results and wasted storage.

bioAF's Reference Dataset layer solves both problems by versioning all reference files and tracking which pipeline runs and analyses used each version.

## Uploading Reference Data

### Step 1: Navigate to Reference Data

Go to **Data Management > Reference Data** and click "Upload Reference."

### Step 2: Define Reference Metadata

Fill in the required fields:

- **Name:** A descriptive, unique name (e.g., "GRCh38 Genome Sequence" or "Cell Ranger GRCh38 2024-A Index").
- **Type:** Select from the dropdown: genome sequence, gene annotation, genome index, cell atlas, marker gene list, ontology, or custom.
- **Organism:** The organism this reference applies to (e.g., Homo sapiens, Mus musculus).
- **Source:** Where the reference data originated (e.g., GENCODE v44, Ensembl 111, 10x Genomics).
- **Source version:** The upstream version identifier.
- **Description:** A brief description of what this reference contains and when to use it.

### Step 3: Upload the File(s)

Drag and drop the reference files or click "Browse" to select them. bioAF supports:

- Single files (FASTA, GTF, GFF3, TSV, h5ad).
- Compressed files (gzip, bzip2). bioAF stores them compressed and decompresses on demand.
- Directories (uploaded as a tarball). Common for genome indices that consist of multiple files.

Upload progress is displayed with an ETA. For large genome indices (10-50GB), expect 5-15 minutes depending on your connection.

### Step 4: Confirm and Publish

After upload, bioAF computes checksums and validates the file format. Review the summary and click "Publish." The reference becomes available to all users and pipelines.

## Versioning

Every reference upload creates a new version. bioAF tracks versions using a combination of the reference name and a monotonically increasing version number.

### How Versions Work

When you upload a new version of an existing reference (same name, same type, same organism), bioAF:

1. Assigns the next version number (v1, v2, v3, etc.).
2. Sets the new version as "Latest."
3. Keeps all previous versions available and accessible.
4. Does not change any existing pipeline configurations -- pipelines that reference a specific version continue to use that version.

### Pinning vs Latest

Pipelines can reference data in two ways:

- **Pinned to a specific version:** The pipeline always uses that exact version regardless of newer uploads. This is the default and the recommended approach for reproducibility.
- **Track latest:** The pipeline automatically uses the newest version. Useful for development and testing but not recommended for production pipelines.

To change a pipeline's reference version, navigate to **Pipelines > Definitions > [Pipeline] > Parameters** and update the reference data parameter.

### Viewing Version History

Navigate to **Data Management > Reference Data > [Reference Name] > Versions** to see all versions with:

- Upload date and uploader.
- File checksums.
- Source version identifier.
- Number of pipeline runs using this version.
- Current status (active, deprecated).

## Deprecation

When a reference version becomes outdated (e.g., a newer genome annotation is available), deprecate it rather than deleting it.

### Step 1: Initiate Deprecation

Navigate to **Data Management > Reference Data > [Reference Name] > Versions** and click "Deprecate" next to the target version.

### Step 2: Provide Reason

Enter a deprecation reason (e.g., "Superseded by GENCODE v45, which corrects annotations for 127 genes"). This reason is displayed to users who have pipelines pinned to the deprecated version.

### Step 3: Set Deprecation Policy

Choose the enforcement level:

- **Warn:** Pipelines using the deprecated version show a warning in the UI and notifications. Runs proceed normally. This is the recommended default.
- **Block new runs:** Existing runs using the deprecated version complete, but new runs cannot be launched. Users must update their pipeline configuration to a newer version.

### Step 4: Confirm

Click "Deprecate." bioAF updates the version status and sends notifications to all users who have pipelines configured with the deprecated version.

### Undoing Deprecation

If a deprecation was premature, navigate to the version and click "Reactivate." This removes the deprecation status and stops warning notifications.

## Impact Assessment

Before uploading a new version or deprecating an old one, assess the impact on existing work.

### Viewing Usage

For any reference version, the "Usage" tab shows:

- **Pipeline definitions** pinned to this version, with links to each pipeline.
- **Pipeline runs** that used this version, with their status and results.
- **Notebook Sessions** that loaded this reference, if the Analysis Snapshot SDK recorded it.

### Comparing Versions

To understand what changed between versions:

1. Navigate to the reference and click "Compare Versions."
2. Select two versions.
3. bioAF displays:
   - File size differences.
   - Checksum differences.
   - For GTF/GFF files: number of genes/transcripts added, removed, or modified.
   - For FASTA files: sequence count and total length comparison.

This comparison helps decide whether a version upgrade requires reprocessing existing data or only applies to new experiments.

### Re-run Assessment

When upgrading a reference version, use **Pipelines > Impact Assessment** to estimate the scope of reprocessing:

1. Select the old reference version and the new reference version.
2. bioAF lists all completed pipeline runs that used the old version.
3. For each run, bioAF estimates the reprocessing cost and time based on the original run's resource usage.
4. Review the total reprocessing cost and decide whether to reprocess all, some, or none.

## Storage and Access

Reference Datasets are stored in a dedicated GCS bucket (`gs://<instance>-reference-data/`) separate from experiment data. This bucket is:

- Read-accessible by all Work Nodes, Notebooks, and Pipelines (pipeline pods and notebook servers).
- Write-accessible only by users with the `admin` or `comp_bio` role.
- Versioned at the GCS object level for additional protection against accidental overwrites.

Reference files are mounted read-only in Work Nodes, Notebooks, and Pipelines at `/ref/` by default. Pipelines access them via this mount path or via the bioAF API.

## Tips

- Always upload reference data through bioAF rather than directly to GCS. Direct uploads bypass versioning and audit logging.
- Use descriptive names that include the source and date (e.g., "GENCODE v44 Human GTF 2024-01") rather than generic names like "human_genome."
- When a new genome build is released, create it as a new reference (different name) rather than a new version of the existing reference. Version increments should represent updates within the same build, not entirely new builds.
- Export your reference data catalog via the API (`GET /api/v1/references/`) to include in publication methods sections. This provides a machine-readable record of exactly which references were used.
- Set up a quarterly review of active references. Deprecating outdated versions proactively is easier than tracking down reproducibility issues months later.
