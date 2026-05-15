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
