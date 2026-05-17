"""Tests for the .md review artifact builder (ADR-052, spec-payload).

Behaviors verified:
- Building for a complete run produces a markdown string that contains the run
  id, parameters, output JSON, QC text, the sample table, and pipeline metadata.
- Building writes the markdown to GCS via the injected writer.
- Missing QC report renders as 'QC report not available' but the build proceeds.
- Missing pipeline output JSON raises ArtifactBuildError (no transmission).
- Large fields are truncated with a marker.
- HTML inside the QC report is stripped to text.
- Never-ship contract: no FASTQ patterns, no stdout/stderr markers, no raw row
  blobs appear in the rendered markdown.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample
from app.services.agent_review_artifact_builder import (
    ArtifactBuildError,
    BuiltArtifact,
    MAX_FIELD_BYTES,
    build_for_run,
    render_run_markdown,
)


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _make_run_with_samples(session: AsyncSession, org_id: int, *, with_output: bool = True) -> int:
    exp = Experiment(name="Test exp", organization_id=org_id, status="processing")
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        pipeline_name="rnaseq",
        pipeline_version="3.14.0",
        parameters_json={"genome": "GRCh38", "trim": True},
        output_files_json={"counts": "gs://x/y/counts.tsv"} if with_output else None,
        status="complete",
    )
    session.add(run)
    await session.flush()

    s1 = Sample(
        experiment_id=exp.id,
        external_id="EXT-001",
        tissue_type="liver",
        qc_status="pass",
        qc_notes="clean",
    )
    s2 = Sample(
        experiment_id=exp.id,
        external_id="EXT-002",
        tissue_type="kidney",
        qc_status="warn",
        qc_notes="low yield",
    )
    session.add_all([s1, s2])
    await session.flush()
    session.add_all(
        [
            PipelineRunSample(pipeline_run_id=run.id, sample_id=s1.id),
            PipelineRunSample(pipeline_run_id=run.id, sample_id=s2.id),
        ]
    )
    await session.commit()
    return run.id


@pytest.mark.asyncio
async def test_build_for_run_renders_all_required_sections(db_engine, admin_user):
    captured: dict[str, str] = {}

    async def writer(path: str, content: str) -> None:
        captured[path] = content

    async with _factory(db_engine)() as session:
        run_id = await _make_run_with_samples(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        artifact = await build_for_run(
            session,
            run_id=run_id,
            qc_report_content="MultiQC: median Q30 = 0.94",
            gcs_writer=writer,
            job_id=42,
        )

    assert isinstance(artifact, BuiltArtifact)
    md = artifact.markdown
    assert "# Pipeline Run Review Input" in md
    assert f"ID: {run_id}" in md
    assert "Pipeline: rnaseq v3.14.0" in md
    assert "genome: GRCh38" in md
    assert "trim: True" in md
    assert "EXT-001" in md and "liver" in md
    assert "EXT-002" in md and "kidney" in md
    assert '"counts": "gs://x/y/counts.tsv"' in md
    assert "median Q30 = 0.94" in md
    assert "## Errors" in md

    assert artifact.gcs_path.endswith("/agent_review_inputs/agent_review_input_job42.md")
    assert artifact.gcs_path in captured
    assert captured[artifact.gcs_path] == md


@pytest.mark.asyncio
async def test_build_for_run_fails_without_output_json(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        run_id = await _make_run_with_samples(session, admin_user.organization_id, with_output=False)
    async with _factory(db_engine)() as session:
        with pytest.raises(ArtifactBuildError):
            await build_for_run(session, run_id=run_id)


@pytest.mark.asyncio
async def test_build_for_run_with_missing_qc_proceeds(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        run_id = await _make_run_with_samples(session, admin_user.organization_id)
    async with _factory(db_engine)() as session:
        artifact = await build_for_run(session, run_id=run_id, qc_report_content=None)
    assert "QC report not available" in artifact.markdown


@pytest.mark.asyncio
async def test_build_for_run_auto_loads_qc_dashboard_when_present(db_engine, admin_user):
    """When no explicit qc_report_content is passed, build_for_run should
    fall back to the QCDashboard row for the run. This was the bug behind
    the production 'QC report not available' artifact: the API never passed
    a qc_report_provider, so the LLM never saw the parsed metrics."""
    from app.models.qc_dashboard import QCDashboard

    async with _factory(db_engine)() as session:
        run_id = await _make_run_with_samples(session, admin_user.organization_id)
        session.add(
            QCDashboard(
                organization_id=admin_user.organization_id,
                pipeline_run_id=run_id,
                metrics_json={
                    "cells": {"estimated_number_of_cells": 1158},
                    "sequencing": {
                        "number_of_reads": 66601887,
                        "sequencing_saturation_pct": 69.7,
                        "q30_bases_in_rna_read_pct": 90.2,
                    },
                    "mapping": {"reads_mapped_to_genome_pct": 95.6},
                },
                summary_text="This run produced 1,158 cells. Overall quality: excellent.",
                status="excellent",
            )
        )
        await session.commit()

    async with _factory(db_engine)() as session:
        artifact = await build_for_run(session, run_id=run_id)

    assert "1,158 cells" in artifact.markdown
    assert "### cells" in artifact.markdown
    assert "### sequencing" in artifact.markdown
    assert "### mapping" in artifact.markdown
    assert "estimated_number_of_cells: 1158" in artifact.markdown
    assert "number_of_reads: 66601887" in artifact.markdown
    assert "sequencing_saturation_pct: 69.7" in artifact.markdown
    assert "QC report not available" not in artifact.markdown


@pytest.mark.asyncio
async def test_explicit_qc_override_wins_over_dashboard(db_engine, admin_user):
    from app.models.qc_dashboard import QCDashboard

    async with _factory(db_engine)() as session:
        run_id = await _make_run_with_samples(session, admin_user.organization_id)
        session.add(
            QCDashboard(
                organization_id=admin_user.organization_id,
                pipeline_run_id=run_id,
                metrics_json={"cells": {"x": 1}},
                summary_text="from dashboard",
                status="ok",
            )
        )
        await session.commit()

    async with _factory(db_engine)() as session:
        artifact = await build_for_run(session, run_id=run_id, qc_report_content="explicit override text")

    assert "explicit override text" in artifact.markdown
    assert "from dashboard" not in artifact.markdown


def test_render_truncates_large_fields():
    big_qc = "x" * (MAX_FIELD_BYTES * 2)
    run = PipelineRun(
        id=999,
        organization_id=1,
        pipeline_name="rnaseq",
        pipeline_version="1",
        parameters_json={"a": 1},
        output_files_json={"o": "v"},
        status="complete",
    )
    md = render_run_markdown(run=run, samples=[], qc_report_content=big_qc)
    assert "[truncated, original size" in md
    # The body of the qc section should be substantially smaller than the
    # original.
    assert len(md.encode("utf-8")) < (MAX_FIELD_BYTES * 2)


def test_render_strips_html_from_qc():
    run = PipelineRun(
        id=1,
        organization_id=1,
        pipeline_name="rnaseq",
        pipeline_version="1",
        parameters_json={"a": 1},
        output_files_json={"o": "v"},
        status="complete",
    )
    md = render_run_markdown(
        run=run,
        samples=[],
        qc_report_content="<html><body><h1>QC</h1><p>Score 99%</p></body></html>",
    )
    assert "Score 99%" in md
    assert "<h1>" not in md
    assert "<body>" not in md


def test_render_never_ship_contract_no_fastq_or_logs_in_output():
    run = PipelineRun(
        id=1,
        organization_id=1,
        pipeline_name="rnaseq",
        pipeline_version="1",
        parameters_json={"a": 1},
        output_files_json={"counts": "path", "qc": "score"},
        status="complete",
    )
    md = render_run_markdown(run=run, samples=[], qc_report_content="benign qc text")
    # Patterns we should never see in the artifact:
    forbidden = [
        "@HISEQ",  # FASTQ header prefix
        ".fastq",
        ".fq.gz",
        "stdout.log",
        "stderr.log",
        "kubectl logs",
    ]
    for pattern in forbidden:
        assert pattern not in md, f"never-ship pattern leaked: {pattern}"


@pytest.mark.asyncio
async def test_build_experiment_header_includes_experiment_metadata_and_all_samples(db_engine, admin_user):
    """Experiment-scope artifact must surface full experiment metadata plus
    every sample in the experiment, regardless of which runs were included.

    The LLM reviewing across the experiment needs the design intent
    (hypothesis, design_type, protocol_version) and per-sample context
    (treatment, donor_source, viability, etc.) to spot cross-run anomalies
    that a per-run review cannot see.
    """
    from datetime import date
    from decimal import Decimal

    from app.services.agent_review_artifact_builder import build_experiment_header

    org_id = admin_user.organization_id
    async with _factory(db_engine)() as session:
        exp = Experiment(
            organization_id=org_id,
            name="PBMC stress assay",
            code="EXP-77",
            external_id="ext-pbmc-77",
            status="processing",
            design_type="dose response",
            hypothesis="High dose increases stress marker expression",
            description="Comparing donor PBMCs across three doses",
            protocol_version="v2.1",
            start_date=date(2026, 4, 1),
            expected_sample_count=6,
            variables_json={"doses_uM": [0, 1, 10]},
        )
        session.add(exp)
        await session.flush()
        s_in = Sample(
            experiment_id=exp.id,
            external_id="PBMC-001",
            organism="Homo sapiens",
            tissue_type="PBMC",
            donor_source="DONOR-A",
            treatment_condition="vehicle",
            viability_pct=Decimal("92.50"),
            cell_count=480000,
            qc_status="pass",
            qc_notes="clean",
            status="processed",
        )
        s_other = Sample(
            experiment_id=exp.id,
            external_id="PBMC-099",
            organism="Homo sapiens",
            tissue_type="PBMC",
            donor_source="DONOR-B",
            treatment_condition="10 uM",
            viability_pct=Decimal("44.10"),
            cell_count=120000,
            qc_status="fail",
            qc_notes="low viability after treatment",
            status="qc_hold",
        )
        session.add_all([s_in, s_other])
        await session.flush()
        # A sample on a DIFFERENT experiment to verify scoping.
        other_exp = Experiment(organization_id=org_id, name="Other", status="registered")
        session.add(other_exp)
        await session.flush()
        s_alien = Sample(
            experiment_id=other_exp.id,
            external_id="ALIEN-001",
            organism="Mus musculus",
        )
        session.add(s_alien)
        await session.commit()
        experiment_id = exp.id

    async with _factory(db_engine)() as session:
        header = await build_experiment_header(
            session,
            experiment_id=experiment_id,
            included_run_ids=[101, 202],
        )

    # Experiment metadata fields surfaced.
    assert "PBMC stress assay" in header
    assert "EXP-77" in header
    assert "ext-pbmc-77" in header
    assert "dose response" in header
    assert "High dose increases stress marker expression" in header
    assert "Comparing donor PBMCs across three doses" in header
    assert "v2.1" in header
    assert "2026-04-01" in header
    assert "doses_uM" in header
    # Included runs surfaced.
    assert "- 101" in header
    assert "- 202" in header
    # Every sample on this experiment surfaced, including ones not in any
    # included run.
    assert "PBMC-001" in header
    assert "PBMC-099" in header
    assert "DONOR-A" in header
    assert "DONOR-B" in header
    assert "vehicle" in header
    assert "10 uM" in header
    assert "low viability after treatment" in header
    # Scoping: a sample from another experiment must NOT appear.
    assert "ALIEN-001" not in header


@pytest.mark.asyncio
async def test_build_experiment_header_handles_no_samples(db_engine, admin_user):
    """An experiment with no samples renders the section with an empty placeholder."""
    from app.services.agent_review_artifact_builder import build_experiment_header

    org_id = admin_user.organization_id
    async with _factory(db_engine)() as session:
        exp = Experiment(organization_id=org_id, name="Empty", status="registered")
        session.add(exp)
        await session.commit()
        experiment_id = exp.id

    async with _factory(db_engine)() as session:
        header = await build_experiment_header(
            session,
            experiment_id=experiment_id,
            included_run_ids=[],
        )
    assert "Empty" in header
    assert "## Samples in this experiment" in header
    assert "_No samples on this experiment._" in header
