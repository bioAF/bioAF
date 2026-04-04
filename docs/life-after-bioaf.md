# Life After bioAF

bioAF is designed with data portability as a core principle (see [ADR-012](../decisions/ADR-012-data-portability.md)). If you stop using the platform, all your data remains accessible through standard tools.

## Preserved Assets

### Database (PostgreSQL)

Your experiment metadata, sample records, pipeline configurations, and audit logs are in PostgreSQL:

- Connect with any PostgreSQL client: `psql`, DBeaver, pgAdmin
- Export with `pg_dump` for migration to another database
- pg_dump backups are stored in GCS per your configured retention policy

### Files (GCS)

All uploaded data files, pipeline outputs, and notebook files are stored in GCS buckets:

- Access via `gsutil`, the GCP Console, or any S3-compatible client
- Bucket names follow the pattern: `bioaf-{purpose}-{org}-{suffix}` (e.g., `bioaf-raw-myorg-abc123`)

### Notebooks

JupyterHub and RStudio notebooks are stored in GCS:

- Download from the results or working bucket
- Notebooks are standard `.ipynb` and `.Rmd` files
- Conda/pip environments are defined in standard `environment.yml` / `requirements.txt` files

### Pipeline Definitions

Nextflow and Snakemake pipeline definitions are standard workflow files:

- Nextflow: `*.nf` files with `nextflow.config`
- Snakemake: `Snakefile` with config YAML
- Run them directly on any compatible compute environment

### Terraform State

Infrastructure state is stored in a GCS backend bucket:

- Import into your own Terraform workspace
- Or use `terraform state list` to see all managed resources

### Platform Configuration

Nightly config backups in GCS contain:

- Component configuration
- User and organization settings
- Notification rules and preferences
- Exportable as JSON

## Recommended Migration Steps

1. **Export database**: use the Backup & Recovery page to trigger a pg_dump, then download from GCS
2. **Copy files**: `gsutil -m cp -r gs://bioaf-raw-myorg-*/ ./local-backup/`
3. **Download notebooks**: download from the GCS working or results bucket
4. **Save pipeline configs**: copy from GCS results bucket
5. **Run teardown**: `bioaf destroy` removes only bioAF-managed infrastructure, not your data buckets (unless explicitly requested)
