"""Tests for the ProvenanceReportService and its components."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.provenance.csv_renderer import CsvRenderer
from app.services.provenance.data_gatherer import ProvenanceDataGatherer
from app.services.provenance.json_renderer import JsonRenderer
from app.services.provenance.markdown_renderer import MarkdownRenderer
from app.services.provenance.pdf_renderer import PdfRenderer
from app.services.provenance.report_service import ProvenanceReportService
from app.services.provenance.schema import SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Helpers to seed test data directly in DB
# ---------------------------------------------------------------------------


async def _seed_experiment_with_data(session, org_id: int, owner_id: int) -> dict:
    """Create an experiment with samples, a pipeline run, processes, files, and audit log entries.

    Returns a dict with IDs of all created entities.
    """
    # Create a project
    await session.execute(
        text(
            "INSERT INTO projects (id, organization_id, name, description, status, owner_user_id, created_by_user_id) "
            "VALUES (1, :org, 'Test Project', 'A test project', 'active', :owner, :owner)"
        ),
        {"org": org_id, "owner": owner_id},
    )

    # Create experiment
    await session.execute(
        text(
            "INSERT INTO experiments (id, organization_id, project_id, name, status, owner_user_id, "
            "design_type, protocol_version, variables_json) "
            "VALUES (1, :org, 1, 'Test Experiment', 'sequencing', :owner, "
            "'case-control', '1.2', :variables)"
        ),
        {"org": org_id, "owner": owner_id, "variables": json.dumps({"treatment": "drug_a"})},
    )

    # Create batch
    await session.execute(
        text(
            "INSERT INTO batches (id, experiment_id, name, instrument_model, operator_user_id) "
            "VALUES (1, 1, 'Batch A', 'NovaSeq 6000', :owner)"
        ),
        {"owner": owner_id},
    )

    # Create samples
    for i in range(1, 4):
        await session.execute(
            text(
                "INSERT INTO samples (id, experiment_id, batch_id, sample_id_external, organism, "
                "tissue_type, qc_status, status, library_prep_method, library_layout, molecule_type, "
                "chemistry_version, donor_source, treatment_condition) "
                "VALUES (:id, 1, 1, :ext, 'Homo sapiens', 'PBMC', 'pass', 'registered', "
                "'10x Chromium', 'paired', 'total RNA', 'v3', 'Donor_1', 'Vehicle')"
            ),
            {"id": i, "ext": f"SAMPLE_{i:03d}"},
        )

    # Create raw file (result file inserted after pipeline run due to FK)
    await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, file_type, size_bytes, "
            "md5_checksum, sha256_checksum, experiment_id, source_type, uploader_user_id, artifact_type) "
            "VALUES (1, :org, 'sample1.fastq.gz', 'gs://bucket/sample1.fastq.gz', 'fastq', 1048576, "
            "'abc123', 'def456', 1, 'upload', :owner, NULL)"
        ),
        {"org": org_id, "owner": owner_id},
    )

    # Link raw file to sample
    await session.execute(text("INSERT INTO sample_files (id, sample_id, file_id) VALUES (1, 1, 1)"))

    # Create pipeline run (before result file that references it)
    await session.execute(
        text(
            "INSERT INTO pipeline_runs (id, organization_id, experiment_id, project_id, "
            "submitted_by_user_id, pipeline_name, pipeline_version, status, "
            "parameters_json, input_files_json, output_files_json, container_versions_json, "
            "reference_genome, alignment_algorithm, retry_count, work_dir, k8s_namespace, k8s_pod_name) "
            "VALUES (1, :org, 1, 1, :owner, 'nf-core/scrnaseq', '2.0.0', 'completed', "
            ":params, :inputs, :outputs, :containers, "
            "'GRCh38', 'STARsolo', 0, '/work/run1', 'bioaf-pipelines', 'run-1-pod')"
        ),
        {
            "org": org_id,
            "owner": owner_id,
            "params": json.dumps({"aligner": "star"}),
            "inputs": json.dumps([{"file_id": 1, "filename": "sample1.fastq.gz"}]),
            "outputs": json.dumps([{"file_id": 2, "filename": "results.h5ad"}]),
            "containers": json.dumps({"star": "quay.io/star:2.7.10b"}),
        },
    )

    # Create result file (references the pipeline run above)
    await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, file_type, size_bytes, "
            "md5_checksum, sha256_checksum, experiment_id, source_type, uploader_user_id, "
            "source_pipeline_run_id, artifact_type) "
            "VALUES (2, :org, 'results.h5ad', 'gs://bucket/results.h5ad', 'h5ad', 52428800, "
            "'xyz789', 'ghi012', 1, 'pipeline_output', :owner, 1, 'feature_matrix')"
        ),
        {"org": org_id, "owner": owner_id},
    )

    # Link sample to pipeline run
    await session.execute(text("INSERT INTO pipeline_run_samples (id, pipeline_run_id, sample_id) VALUES (1, 1, 1)"))

    # Create pipeline process
    await session.execute(
        text(
            "INSERT INTO pipeline_processes (id, pipeline_run_id, process_name, task_id, status, "
            "exit_code, cpu_usage, memory_peak_gb, duration_seconds) "
            "VALUES (1, 1, 'FASTQC', 'task_001', 'completed', 0, 2.5, 4.2, 120)"
        )
    )

    # Add project sample link
    await session.execute(
        text("INSERT INTO project_samples (id, project_id, sample_id, added_by_user_id) VALUES (1, 1, 1, :owner)"),
        {"owner": owner_id},
    )

    # Create audit log entries
    for entity_type, entity_id, action in [
        ("experiment", 1, "create"),
        ("experiment", 1, "status_change"),
        ("sample", 1, "create"),
        ("pipeline_run", 1, "submit"),
        ("pipeline_run", 1, "complete"),
        ("file", 1, "upload"),
        ("project", 1, "create"),
    ]:
        await session.execute(
            text(
                "INSERT INTO audit_log (user_id, entity_type, entity_id, action, details_json) "
                "VALUES (:uid, :etype, :eid, :action, :details)"
            ),
            {
                "uid": owner_id,
                "etype": entity_type,
                "eid": entity_id,
                "action": action,
                "details": json.dumps({"info": f"{action} {entity_type}"}),
            },
        )

    await session.commit()

    return {
        "project_id": 1,
        "experiment_id": 1,
        "sample_ids": [1, 2, 3],
        "file_ids": [1, 2],
        "pipeline_run_id": 1,
        "batch_id": 1,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_data(session, admin_user):
    """Seed a full set of provenance test data."""
    return await _seed_experiment_with_data(session, admin_user.organization_id, admin_user.id)


@pytest_asyncio.fixture
async def experiment_data(session, seeded_data, admin_user):
    """Gather experiment provenance data."""
    return await ProvenanceDataGatherer.gather_experiment(
        session, seeded_data["experiment_id"], admin_user.organization_id
    )


@pytest_asyncio.fixture
async def experiment_json(experiment_data):
    """Render experiment provenance data to JSON."""
    return JsonRenderer.render("experiment", experiment_data, "admin@test.com")


# ---------------------------------------------------------------------------
# Data Gatherer Tests
# ---------------------------------------------------------------------------


class TestDataGatherer:
    @pytest.mark.asyncio
    async def test_gather_project_provenance(self, session, seeded_data, admin_user):
        data = await ProvenanceDataGatherer.gather_project(
            session, seeded_data["project_id"], admin_user.organization_id
        )
        assert data.project["id"] == 1
        assert data.project["name"] == "Test Project"
        assert len(data.experiments) >= 1
        assert len(data.pipeline_runs) >= 1
        assert len(data.audit_trail) >= 1

    @pytest.mark.asyncio
    async def test_gather_experiment_provenance(self, session, seeded_data, admin_user):
        data = await ProvenanceDataGatherer.gather_experiment(
            session, seeded_data["experiment_id"], admin_user.organization_id
        )
        assert data.experiment["id"] == 1
        assert data.experiment["name"] == "Test Experiment"
        assert len(data.samples) == 3
        assert len(data.pipeline_runs) >= 1
        assert len(data.files_raw) >= 1
        assert len(data.files_results) >= 1
        assert len(data.audit_trail) >= 1

    @pytest.mark.asyncio
    async def test_gather_sample_provenance(self, session, seeded_data, admin_user):
        data = await ProvenanceDataGatherer.gather_sample(session, 1, admin_user.organization_id)
        assert data.sample["id"] == 1
        assert data.sample["external_id"] == "SAMPLE_001"
        assert len(data.files) >= 1
        assert len(data.pipeline_runs) >= 1
        assert data.batch is not None
        assert data.batch["name"] == "Batch A"

    @pytest.mark.asyncio
    async def test_gather_pipeline_run_provenance(self, session, seeded_data, admin_user):
        data = await ProvenanceDataGatherer.gather_pipeline_run(session, 1, admin_user.organization_id)
        assert data.run["id"] == 1
        assert data.run["pipeline_name"] == "nf-core/scrnaseq"
        assert len(data.processes) >= 1
        assert len(data.output_files) >= 1
        assert len(data.samples) >= 1

    @pytest.mark.asyncio
    async def test_gather_artifact_provenance(self, session, seeded_data, admin_user):
        data = await ProvenanceDataGatherer.gather_artifact(session, 2, admin_user.organization_id)
        assert data.file["id"] == 2
        assert data.file["filename"] == "results.h5ad"
        assert data.source_pipeline_run is not None
        assert data.source_pipeline_run["id"] == 1

    @pytest.mark.asyncio
    async def test_gather_respects_org_isolation(self, session, seeded_data, admin_user):
        """Data from a different org should not be returned."""
        other_org_id = admin_user.organization_id + 999
        data = await ProvenanceDataGatherer.gather_experiment(session, seeded_data["experiment_id"], other_org_id)
        # Experiment belongs to a different org so gatherer should return empty data
        assert data.experiment == {}


# ---------------------------------------------------------------------------
# JSON Renderer Tests
# ---------------------------------------------------------------------------


class TestJsonRenderer:
    def test_json_schema_version(self, experiment_json):
        assert experiment_json["schema_version"] == SCHEMA_VERSION

    def test_json_experiment_structure(self, experiment_json):
        assert experiment_json["report_type"] == "experiment"
        assert "generated_at" in experiment_json
        assert "generated_by" in experiment_json
        assert experiment_json["generated_by"] == "admin@test.com"
        entity = experiment_json["entity"]
        assert entity["type"] == "experiment"
        assert entity["id"] == 1
        assert "samples" in entity
        assert "pipeline_runs" in entity
        assert "files" in entity
        assert "audit_trail" in experiment_json

    def test_json_null_fields_included(self, experiment_json):
        """New nullable provenance fields should appear as null, not omitted."""
        entity = experiment_json["entity"]
        # design_type was set, but collection fields on samples should be null
        samples = entity["samples"]
        assert len(samples) > 0
        first_sample = samples[0]
        assert "collection" in first_sample
        assert first_sample["collection"]["timestamp"] is None
        assert first_sample["collection"]["method"] is None


# ---------------------------------------------------------------------------
# Markdown Renderer Tests
# ---------------------------------------------------------------------------


class TestMarkdownRenderer:
    def test_markdown_contains_tables(self, experiment_json):
        md = MarkdownRenderer.render("experiment", experiment_json)
        assert "| " in md
        assert "| --- |" in md or "|---|" in md or "| --- " in md

    def test_markdown_experiment_sections(self, experiment_json):
        md = MarkdownRenderer.render("experiment", experiment_json)
        assert "## Experiment Metadata" in md or "## Metadata" in md
        assert "## Samples" in md
        assert "## Pipeline Runs" in md
        assert "## Files" in md
        assert "## Audit Trail" in md


# ---------------------------------------------------------------------------
# CSV Renderer Tests
# ---------------------------------------------------------------------------


class TestCsvRenderer:
    def test_csv_sample_manifest_columns(self, experiment_json):
        csv_files = CsvRenderer.render("experiment", experiment_json)
        assert "sample_manifest.csv" in csv_files
        manifest = csv_files["sample_manifest.csv"]
        header_line = manifest.split("\n")[0]
        # Should contain key columns
        for col in ["Sample ID", "External ID", "Organism", "Tissue Type", "QC Status"]:
            assert col in header_line

    def test_csv_file_manifest_includes_checksums(self, experiment_json):
        csv_files = CsvRenderer.render("experiment", experiment_json)
        assert "file_manifest.csv" in csv_files
        manifest = csv_files["file_manifest.csv"]
        header_line = manifest.split("\n")[0]
        assert "MD5" in header_line
        assert "SHA-256" in header_line

    def test_csv_pipeline_runs_one_row_per_run(self, experiment_json):
        csv_files = CsvRenderer.render("experiment", experiment_json)
        assert "pipeline_runs.csv" in csv_files
        runs_csv = csv_files["pipeline_runs.csv"]
        lines = [line for line in runs_csv.strip().split("\n") if line]
        run_count = len(experiment_json["entity"]["pipeline_runs"])
        # header + data rows
        assert len(lines) == 1 + run_count


# ---------------------------------------------------------------------------
# PDF Renderer Tests
# ---------------------------------------------------------------------------


class TestPdfRenderer:
    def test_pdf_generation_returns_bytes(self, experiment_json):
        pdf_bytes = PdfRenderer.render("experiment", experiment_json)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0

    def test_pdf_starts_with_pdf_header(self, experiment_json):
        pdf_bytes = PdfRenderer.render("experiment", experiment_json)
        assert pdf_bytes[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    async def test_generate_all_formats(self, session, seeded_data, admin_user):
        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="experiment",
            entity_id=seeded_data["experiment_id"],
            org_id=admin_user.organization_id,
            user_email="admin@test.com",
            format="all",
        )
        assert result.content_type == "application/zip"
        assert result.filename.endswith(".zip")

        # Verify ZIP contents
        zf = zipfile.ZipFile(BytesIO(result.content))  # type: ignore[arg-type]
        names = zf.namelist()
        assert any(n.endswith(".json") for n in names)
        assert any(n.endswith(".md") for n in names)
        assert any(n.endswith(".pdf") for n in names)
        assert any(n.endswith(".csv") for n in names)

    @pytest.mark.asyncio
    async def test_artifact_downstream_usage_integer_array_format(self, session, admin_user):
        """gather_artifact should find downstream pipeline runs when input_files_json
        stores plain integer arrays (the format written by trigger_service)."""
        org_id = admin_user.organization_id

        # Raw input file
        await session.execute(
            text(
                "INSERT INTO files (id, organization_id, filename, gcs_uri, file_type, size_bytes, source_type) "
                "VALUES (800, :org, 'raw.fastq.gz', 'gs://bucket/raw.fastq.gz', 'fastq', 1000, 'upload')"
            ),
            {"org": org_id},
        )

        # Pipeline run that consumed the file -- stored as plain integer array (production format)
        await session.execute(
            text(
                "INSERT INTO pipeline_runs (id, organization_id, pipeline_name, status, input_files_json) "
                "VALUES (800, :org, 'nf-core/scrnaseq', 'completed', :inputs)"
            ),
            {"org": org_id, "inputs": json.dumps([800])},
        )
        await session.commit()

        data = await ProvenanceDataGatherer.gather_artifact(session, 800, org_id)
        assert len(data.downstream_usage) == 1, (
            f"Expected 1 downstream run, got {len(data.downstream_usage)}: {data.downstream_usage}"
        )
        assert data.downstream_usage[0]["pipeline_run_id"] == 800

    @pytest.mark.asyncio
    async def test_empty_experiment_report(self, session, admin_user):
        """Experiment with no samples or runs should produce a valid report."""
        await session.execute(
            text(
                "INSERT INTO experiments (id, organization_id, name, status, owner_user_id) "
                "VALUES (999, :org, 'Empty Experiment', 'registered', :owner)"
            ),
            {"org": admin_user.organization_id, "owner": admin_user.id},
        )
        await session.commit()

        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="experiment",
            entity_id=999,
            org_id=admin_user.organization_id,
            user_email="admin@test.com",
            format="json",
        )
        report = json.loads(result.content)
        assert report["schema_version"] == SCHEMA_VERSION
        assert report["entity"]["samples"] == []
        assert report["entity"]["pipeline_runs"] == []
