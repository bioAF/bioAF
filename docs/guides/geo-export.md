# GEO Export

bioAF automates the preparation and submission of data to NCBI's Gene Expression Omnibus (GEO). This guide covers exporting single experiments, creating SuperSeries from multiple experiments, validating submissions, and uploading to GEO's FTP server.

## Overview

A GEO submission requires:

- A completed metadata spreadsheet (Excel format) with experiment, sample, and protocol information.
- Raw data files (FASTQs) with MD5 checksums.
- Processed data files (count matrices, normalized expression tables) with MD5 checksums.

bioAF generates all of these from your existing experiment metadata and data files, eliminating the manual transcription that typically takes hours per submission.

## Single Experiment Export

### Step 1: Verify Experiment Readiness

Before exporting, confirm that your experiment has all required MINSEQE metadata fields populated. Navigate to **Experiments > [Your Experiment] > Metadata** and check for completeness:

- Organism and tissue type.
- Library preparation protocol (including kit and version).
- Sequencing platform and read configuration.
- At least one processed data file linked to each sample.
- Experiment description (abstract-quality, 100-300 words).

If any required fields are missing, bioAF highlights them with a warning icon. Fill these in before proceeding.

### Step 2: Initiate Export

Navigate to **Experiments > [Your Experiment] > Export** and click "GEO Export." bioAF presents a pre-export summary:

- Number of samples to include.
- Number of raw files (FASTQs) and processed files.
- Estimated total upload size.
- Any metadata warnings or missing fields.

Review the summary. You can exclude specific samples by unchecking them in the sample list.

### Step 3: Configure Export Options

- **Series type:** Select the appropriate GEO series type (Expression profiling by high throughput sequencing, Genome binding/occupancy profiling, etc.).
- **Release date:** Choose "Immediately upon GEO processing" or set a hold date (common for pre-publication submissions). You can update this later through GEO's interface.
- **Contact information:** bioAF pre-fills from your profile. Verify the submitter name, email, institution, and address.

### Step 4: Generate the Submission Package

Click "Generate." bioAF creates:

- A GEO-formatted Excel metadata spreadsheet with all required sheets (SERIES, SAMPLES, PROTOCOLS, DATA PROCESSING).
- MD5 checksum files for all raw and processed data.
- A manifest file listing all files to upload.

The generation process takes 1-5 minutes depending on the number of files (checksum computation is the bottleneck). When complete, the package appears in **Experiments > [Your Experiment] > Export > Packages**.

### Step 5: Review the Spreadsheet

Download the generated Excel file and review it. Pay special attention to:

- Sample titles and descriptions (GEO reviewers often request revisions here).
- Protocol descriptions (should be detailed enough to reproduce the experiment).
- Data processing steps (list each pipeline step and software version).

You can edit the spreadsheet manually if needed. bioAF preserves your edits if you re-upload the modified file.

## SuperSeries Export

A SuperSeries groups multiple related experiments (SubSeries) under a single GEO accession. This is common for publications that span multiple experimental conditions or timepoints.

### Step 1: Create a Project

If you have not already, create a Project containing all the experiments you want to include. Navigate to **Projects > Create** and add the relevant experiments.

### Step 2: Initiate SuperSeries Export

From the project view, click **Export > GEO SuperSeries**. bioAF treats each constituent experiment as a SubSeries.

### Step 3: Configure the SuperSeries

- **SuperSeries title:** The overarching title for the combined submission.
- **SuperSeries summary:** A description that explains how the SubSeries relate to each other.
- **SubSeries order:** Drag experiments into the desired order.

### Step 4: Generate and Review

bioAF generates one metadata spreadsheet per SubSeries plus a SuperSeries metadata sheet. Review each spreadsheet individually, then verify the SuperSeries sheet correctly references all SubSeries.

## Validation

bioAF validates the submission package against GEO's requirements before you upload. Validation runs automatically during generation and can be re-run manually.

### Automatic Validation Checks

- **Required fields:** All mandatory GEO fields are populated.
- **Controlled vocabularies:** Organism names match NCBI taxonomy, molecule types match GEO's accepted values.
- **File integrity:** MD5 checksums match the actual files. Files are not truncated or corrupted.
- **Cross-references:** Every sample has at least one raw data file and one processed data file linked.
- **Consistency:** Library strategy, source, and selection values are internally consistent.

### Viewing Validation Results

Navigate to **Experiments > [Your Experiment] > Export > Packages > [Package] > Validation**. Results are categorized:

- **Errors:** Must be fixed before submission. GEO will reject the package.
- **Warnings:** Should be reviewed but will not cause rejection.
- **Info:** Informational notes about optional fields or best practices.

### Fixing Validation Errors

Click on any error to see the affected field and suggested fix. Most errors can be resolved by updating experiment metadata in bioAF and regenerating the package. Common errors:

- Missing organism in NCBI taxonomy -- verify the species name matches exactly.
- No processed data files for a sample -- link the correct output files from the pipeline run.
- Protocol description too short -- GEO requires substantive protocol text, not just a kit name.

## FTP Upload

### Step 1: Obtain GEO Credentials

GEO provides FTP credentials when you create a submission account. If you do not have credentials, register at the [GEO submission portal](https://www.ncbi.nlm.nih.gov/geo/info/submission.html).

### Step 2: Configure FTP in bioAF

Navigate to **Settings > Integrations > GEO FTP** and enter:

- **FTP host:** ftp-private.ncbi.nlm.nih.gov (default, rarely changes).
- **Username:** Your GEO FTP username.
- **Password:** Your GEO FTP password. Stored encrypted in Google Secret Manager.

### Step 3: Upload

From the export package view, click "Upload to GEO." bioAF:

1. Connects to the GEO FTP server.
2. Creates a submission directory named with your GEO username and a timestamp.
3. Uploads the metadata spreadsheet.
4. Uploads all raw and processed data files with progress tracking.
5. Verifies each uploaded file's MD5 against the local checksum.

Upload progress is displayed in the UI. For large submissions (>100GB), expect several hours. bioAF handles retries for interrupted transfers automatically.

### Step 4: Notify GEO

After upload completes, bioAF generates a notification email to GEO curators with your submission details. Review the email content and click "Send" to notify GEO that your submission is ready for processing. GEO typically processes submissions within 3-5 business days.

## Tips

- Start the GEO export process early, even before your manuscript is finalized. Reviewers frequently request the GEO accession number during peer review, and GEO processing can take up to a week.
- Use the hold date feature to keep your data private until publication. You can extend the hold date through GEO's interface if your publication timeline changes.
- For large submissions, start the FTP upload at the end of the workday and let it run overnight. bioAF sends a notification when the upload completes.
- Save validation reports as PDF (available from the validation view) for your records. Some journals require evidence of data repository submission.
- If GEO curators request revisions to your metadata, make the changes in bioAF's experiment metadata, regenerate the package, and re-upload. This preserves the Audit Log of what was submitted.
