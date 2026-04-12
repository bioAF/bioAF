# CRO Naming Profiles

When Contract Research Organizations (CROs) deliver data files, they follow structured naming conventions that encode project, experiment, sample, date, and version information into each filename. bioAF's naming profiles let you define parsing rules that automatically extract this metadata during auto-ingest, linking incoming files to the correct experiments and samples without manual intervention.

## How Naming Profiles Work

A naming profile is a set of rules that describe the structure of filenames from a specific CRO. Each profile defines:

- **Delimiter:** The character separating fields (typically underscore `_` or hyphen `-`).
- **Field positions:** Which position in the delimited filename maps to which metadata field (project, experiment, sample, date, data type, researcher, version).
- **Date format:** How the CRO encodes dates (e.g., `YYYY-MM-DD`, `YYMMDD`, `DD-Mon-YYYY`).
- **Version pattern:** How version numbers are encoded (e.g., `v001`, `V1`, `rev2`).
- **File type mappings:** Which filename patterns or extensions correspond to which bioAF data types (FASTQ, BAM, count matrix, QC report).

When a file arrives in the auto-ingest bucket, bioAF tries each active naming profile against the filename. The first profile that successfully parses all required fields is used to catalog the file.

## Creating a Naming Profile

### Step 1: Navigate to Naming Profiles

Go to **Data & Files > Naming Profiles** and click "Create Profile."

### Step 2: Define Basic Information

- **Profile name:** A descriptive name like "GeneCorp Standard" or "SequenceLab 2026."
- **CRO name:** The CRO this profile applies to. This is used for display and filtering.
- **Priority:** Profiles are tried in priority order (lower number = higher priority). Set this to control which profile takes precedence when multiple could match.

### Step 3: Configure the Delimiter and Fields

Enter a sample filename from the CRO. bioAF splits it by the selected delimiter and displays the resulting fields in a visual editor.

Example filename: `2026-03-10_ProjectX_RNASeq_DiffExpr_SmithE_v001.txt`

With underscore delimiter, this produces six fields:

| Position | Value | Mapping |
|----------|-------|---------|
| 1 | `2026-03-10` | Date |
| 2 | `ProjectX` | Project Code |
| 3 | `RNASeq` | Data Type |
| 4 | `DiffExpr` | Experiment Code |
| 5 | `SmithE` | Researcher |
| 6 | `v001` | Version |

Use the dropdown for each position to assign it to a bioAF metadata field. Fields marked as "Ignore" are parsed but not used for cataloging.

### Step 4: Configure Code Mappings

CRO-specific codes rarely match bioAF's internal names exactly. Code mappings translate between the two.

**Data type mappings** link CRO codes to bioAF data types:

| CRO Code | bioAF Data Type |
|----------|-----------------|
| `RNASeq` | `fastq` |
| `DiffExpr` | `count_matrix` |
| `QCReport` | `qc_report` |
| `Alignment` | `bam` |

**Project code mappings** link CRO project identifiers to bioAF experiments:

| CRO Code | bioAF Experiment |
|----------|------------------|
| `ProjectX` | `EXP-2026-001` |
| `ProjY` | `EXP-2026-002` |

Navigate to the "Code Mappings" tab in the profile editor to define these. You can add mappings individually or bulk-import from a CSV file.

### Step 5: Set the Date Format

Select the date format used by the CRO from the dropdown, or enter a custom format string using Python's strftime syntax (e.g., `%Y-%m-%d` for `2026-03-10`, `%d%b%Y` for `10Mar2026`).

### Step 6: Set the Version Pattern

Enter a regex pattern that matches version strings in filenames. Common patterns:

- `v\d{3}` matches `v001`, `v002`
- `V\d+` matches `V1`, `V12`
- `rev\d+` matches `rev1`, `rev2`

bioAF uses the version to determine which file supersedes a previous delivery. Higher versions replace lower versions for the same experiment/sample/data-type combination.

### Step 7: Save and Activate

Click "Save Profile." The profile is created in inactive state. Review the configuration, then toggle the "Active" switch to enable it.

## Testing a Naming Profile

Before activating a profile for production use, test it against sample filenames.

### Step 1: Open the Test Panel

From the profile editor, click "Test Profile" in the toolbar.

### Step 2: Enter Test Filenames

Paste a list of filenames (one per line) from a recent CRO delivery. bioAF parses each filename against the profile and displays the extracted metadata in a results table.

### Step 3: Review Results

For each filename, verify:

- All fields are correctly extracted (date, project, experiment, sample, data type, version).
- Code mappings resolve to the correct bioAF entities.
- No fields show "Unresolved" -- this means a code mapping is missing.
- The date parses correctly (watch for timezone and format issues).

### Step 4: Fix and Re-test

If any fields are incorrect, adjust the profile configuration and re-run the test. Common issues:

- **Wrong delimiter:** Some CROs use hyphens within date fields and underscores between fields. Use the "Compound delimiter" option to handle this.
- **Missing code mappings:** Add new mappings for any CRO codes that show as "Unresolved."
- **Variable field count:** Some CROs include optional fields. Use the "Optional field" checkbox for positions that may be absent.

## Managing Multiple Profiles

When working with multiple CROs simultaneously, each CRO gets its own profile. Profiles are tried in priority order during auto-ingest. If the first profile fails to parse a filename (missing required fields or unresolved codes), bioAF tries the next profile.

To reorder priorities, go to **Data & Files > Naming Profiles** and drag profiles into the desired order, or edit the priority number directly.

## Tips

- Always test a profile against at least 20-30 real filenames before activating it. Edge cases in CRO naming are common.
- When a CRO changes their naming convention (even slightly), create a new profile version rather than editing the existing one. This preserves the parsing history for files already ingested under the old convention.
- Use the "Fallback action" setting to control what happens when no profile matches a file: quarantine (default), notify admin, or ignore. Quarantined files appear in **Data Management > Quarantine** for manual review.
- Export your naming profiles as JSON backups via the API (`GET /api/v1/naming-profiles/export`). This is useful when setting up a second bioAF instance or sharing profiles between teams.
