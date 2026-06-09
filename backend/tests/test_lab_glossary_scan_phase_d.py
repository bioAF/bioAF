"""Lab Glossary scan -- Phase D source changes (LK-SPEC-D, D1 + D2).

Covers the replacement of the ``topic`` source with ``experiment`` (reusing the
Experiment Review context builder, NOT associated-file content) and the
generalized ``document`` source that resolves ``file:``/``lab_document:``/bare-int
inputs to the right GCS object. LLM and GCS I/O are injected so these tests
exercise pure DB + assembly behavior. Covers AC-D01..D06, AC-D12.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.exceptions import ValidationError

from app.models.experiment import Experiment
from app.models.file import File
from app.models.lab_document import LabDocument, LabDocumentVersion
from app.models.lab_glossary import LabGlossaryScanJob, LabGlossaryScanProposal
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.qc_dashboard import QCDashboard
from app.models.sample import Sample, sample_files
from app.services import lab_glossary_scan_service as scan_svc


def _factory(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def _proposals(session, job_id):
    return (
        (await session.execute(select(LabGlossaryScanProposal).where(LabGlossaryScanProposal.scan_job_id == job_id)))
        .scalars()
        .all()
    )


async def _seed_experiment_with_run(session: AsyncSession, org_id: int) -> int:
    """Experiment + one complete run with a QC dashboard + an associated file.

    The associated file is deliberately given a distinctive filename so a test
    can assert the experiment scan does NOT pull in associated-file content.
    """
    exp = Experiment(
        name="Oocyte maturation study",
        organization_id=org_id,
        status="processing",
        hypothesis="Maturation media improves viability.",
    )
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        pipeline_name="scrnaseq",
        pipeline_version="1.0.0",
        parameters_json={"genome": "GRCh38"},
        output_files_json={"counts": "gs://x/y/counts.tsv"},
        status="complete",
    )
    session.add(run)
    await session.flush()

    sample = Sample(experiment_id=exp.id, external_id="EXT-OO-1", tissue_type="ovary", qc_status="pass")
    session.add(sample)
    await session.flush()
    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id))

    session.add(
        QCDashboard(
            organization_id=org_id,
            pipeline_run_id=run.id,
            experiment_id=exp.id,
            metrics_json={"alignment": {"mapped_pct": 91.2}},
            summary_text="Cryoprotectant exposure within tolerance.",
            status="complete",
        )
    )

    # Associated file whose content must never appear in the experiment scan.
    f = File(
        organization_id=org_id,
        gcs_uri="gs://bucket/ZZZSECRETFILE.txt",
        filename="ZZZSECRETFILE.txt",
        file_type="txt",
        experiment_id=exp.id,
        source_type="upload",
    )
    session.add(f)
    await session.flush()
    await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.commit()
    return exp.id


# --- create_scan_job source set (D1) ----------------------------------------


@pytest.mark.asyncio
async def test_create_scan_job_accepts_experiment(session, admin_user):
    # AC-D01, AC-D02
    org_id, uid = admin_user.organization_id, admin_user.id
    job = await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="experiment", scan_input="123")
    assert job.scan_type == "experiment" and job.scan_input == "123"


@pytest.mark.asyncio
async def test_create_scan_job_rejects_topic(session, admin_user):
    # AC-D01: topic is no longer a valid NEW scan source.
    org_id, uid = admin_user.organization_id, admin_user.id
    with pytest.raises(ValidationError):
        await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="topic", scan_input="x")


@pytest.mark.asyncio
async def test_create_scan_job_rejects_non_numeric_experiment(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    with pytest.raises(ValidationError):
        await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="experiment", scan_input="abc")


@pytest.mark.asyncio
async def test_create_scan_job_validates_document_input(session, admin_user):
    # AC-D06: bad prefix / non-numeric id rejected at job creation.
    org_id, uid = admin_user.organization_id, admin_user.id
    for good in ("file:7", "lab_document:7", "7"):
        job = await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="document", scan_input=good)
        assert job.scan_type == "document"
    for bad in ("bogus:7", "file:abc", "file:", "", None):
        with pytest.raises(ValidationError):
            await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="document", scan_input=bad)


# --- experiment content collection (D1, F-LKD-02) ----------------------------


@pytest.mark.asyncio
async def test_collect_experiment_content_includes_run_and_qc_not_files(session, admin_user, db_engine):
    # AC-D03
    org_id, uid = admin_user.organization_id, admin_user.id
    exp_id = await _seed_experiment_with_run(session, org_id)
    job = await scan_svc.create_scan_job(
        session, org_id=org_id, user_id=uid, scan_type="experiment", scan_input=str(exp_id)
    )
    await session.commit()

    async with _factory(db_engine)() as s2:
        fresh = (await s2.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job.id))).scalar_one()
        payload = await scan_svc._collect_experiment_content(s2, fresh)

    assert "Oocyte maturation study" in payload
    assert "scrnaseq" in payload
    assert "Cryoprotectant exposure within tolerance." in payload  # QC dashboard text
    assert "ZZZSECRETFILE" not in payload  # associated-file content excluded


@pytest.mark.asyncio
async def test_collect_experiment_content_cross_org_fails(session, admin_user, db_engine):
    # AC-D04
    from app.models.organization import Organization

    org_id, uid = admin_user.organization_id, admin_user.id
    # Experiment in a DIFFERENT (real) org; the scan job runs in admin's org.
    other_org = Organization(name="Other Org", setup_complete=True)
    session.add(other_org)
    await session.flush()
    other = Experiment(name="Other org exp", organization_id=other_org.id, status="processing")
    session.add(other)
    await session.flush()
    job = await scan_svc.create_scan_job(
        session, org_id=org_id, user_id=uid, scan_type="experiment", scan_input=str(other.id)
    )
    await session.commit()
    async with _factory(db_engine)() as s2:
        fresh = (await s2.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job.id))).scalar_one()
        with pytest.raises(Exception):
            await scan_svc._collect_experiment_content(s2, fresh)


@pytest.mark.asyncio
async def test_execute_experiment_scan_produces_proposals(session, admin_user, db_engine):
    # AC-D03 (end to end via execute_scan with a stubbed submit)
    org_id, uid = admin_user.organization_id, admin_user.id
    exp_id = await _seed_experiment_with_run(session, org_id)
    job = await scan_svc.create_scan_job(
        session, org_id=org_id, user_id=uid, scan_type="experiment", scan_input=str(exp_id)
    )
    await session.commit()

    captured = {}

    async def fake_submit(*, prompt, payload, model, api_key):
        captured["prompt"] = prompt
        captured["payload"] = payload
        return '[{"term": "Cryoprotectant", "definition": "Protects cells during freezing."}]'

    await scan_svc.execute_scan(_factory(db_engine), job_id=job.id, submit_override=fake_submit)

    # Uses the extraction prompt, not a topic-generation prompt.
    assert "Oocyte maturation study" in captured["payload"]
    props = await _proposals(session, job.id)
    assert {p.term for p in props} == {"Cryoprotectant"}
    async with _factory(db_engine)() as s2:
        refreshed = (await s2.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job.id))).scalar_one()
        assert refreshed.status == "complete"


# --- document source resolution across both stores (D2, F-LKD-03) -----------


@pytest.mark.asyncio
async def test_extract_document_text_dispatches_file_and_lab_document(session, admin_user, db_engine, monkeypatch):
    # AC-D05, AC-D12 (bare int == lab_document)
    org_id, uid = admin_user.organization_id, admin_user.id

    doc = LabDocument(
        organization_id=org_id,
        title="SOP",
        gcs_uri="gs://docs/sop_v1.pdf",
        current_version=1,
        file_name="sop.pdf",
        created_by_user_id=uid,
    )
    session.add(doc)
    await session.flush()
    session.add(
        LabDocumentVersion(
            document_id=doc.id,
            version_number=1,
            gcs_uri="gs://docs/sop_v1.pdf",
            file_name="sop.pdf",
            uploaded_by_user_id=uid,
        )
    )
    f = File(
        organization_id=org_id,
        gcs_uri="gs://files/protocol.txt",
        filename="protocol.txt",
        file_type="txt",
        source_type="upload",
    )
    session.add(f)
    await session.flush()
    await session.commit()

    seen_uris = []

    async def fake_extract(s, gcs_uri):
        seen_uris.append(gcs_uri)
        return f"text of {gcs_uri}"

    import app.services.lab_glossary_extraction as extraction_mod

    monkeypatch.setattr(extraction_mod, "extract_text_from_gcs", fake_extract)

    async def run_one(scan_input):
        seen_uris.clear()
        async with _factory(db_engine)() as s2:
            job = LabGlossaryScanJob(
                organization_id=org_id,
                scan_type="document",
                scan_input=scan_input,
                status="running",
                initiated_by_user_id=uid,
            )
            s2.add(job)
            await s2.flush()
            text = await scan_svc._extract_document_text(s2, job)
        return text, list(seen_uris)

    text, uris = await run_one(f"file:{f.id}")
    assert uris == ["gs://files/protocol.txt"]
    text, uris = await run_one(f"lab_document:{doc.id}")
    assert uris == ["gs://docs/sop_v1.pdf"]
    text, uris = await run_one(str(doc.id))  # bare int -> lab document
    assert uris == ["gs://docs/sop_v1.pdf"]
