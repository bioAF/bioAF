"""01C.2: file.registered / file.ready from the reachable registration owners.

Registration is announced once, after the workflow has set the file's external ID, context and sample
links; readiness is announced once, when the final object is verified available. These tests use the
real local-mode object store, so readiness observes real bytes.
"""

from __future__ import annotations

import hashlib
import io
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ConflictError, ValidationError
from app.models.experiment import Experiment
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.project import Project
from app.models.sample import Sample
from app.services import file_verification_worker
from app.services.pipeline_output_service import PipelineOutputService
from app.services.upload_service import UploadService
from tests.support.uploads import seed_signed_upload
from tests.support.webhook_capture import WebhookCapture

pytestmark = pytest.mark.asyncio

REGISTERED, READY = "file.registered", "file.ready"
DATA = b"@r\nACGT\n+\nIIII\n" * 100
MD5 = hashlib.md5(DATA).hexdigest()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import s3 as module

    monkeypatch.setattr(module, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = S3StorageProvider(org_slug="t")
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
    return adapter


@pytest_asyncio.fixture
async def capture(session, admin_user):
    c = await WebhookCapture.start(session, admin_user)
    yield c
    c.close()


@pytest_asyncio.fixture
async def tree(session, admin_user):
    project = Project(organization_id=admin_user.organization_id, name="P", external_id="PRJ-1")
    session.add(project)
    await session.flush()
    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project.id,
        name="E",
        external_id="LIMS-EXP-042",
        status="analysis",
    )
    session.add(exp)
    await session.flush()
    s1 = Sample(experiment_id=exp.id, external_id="LIMS-SMP-1001", status="registered")
    s2 = Sample(experiment_id=exp.id, external_id=None, status="registered")
    session.add_all([s1, s2])
    await session.commit()
    return project, exp, s1, s2


async def _pending(admin_user, uri, *, external_id=None, expected_md5=None, sample_ids=(), size=None):
    upload_id = str(uuid.uuid4())
    await seed_signed_upload(
        upload_id=upload_id,
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        filename=uri.rsplit("/", 1)[-1],
        gcs_uri=uri,
        expected_size=size,
        expected_md5=expected_md5,
        project_id=None,
        experiment_id=None,
        sample_ids=list(sample_ids),
        is_global=False,
        external_id=external_id,
    )
    return upload_id


# --- uploads ----------------------------------------------------------------------------------


async def test_a_completed_upload_is_registered_with_its_links_then_ready(session, admin_user, store, tree, capture):
    project, exp, s1, s2 = tree
    uri = "s3://bioaf-ingest-t/uploads/u1/reads.fastq.gz"
    await store.write_bytes(uri, DATA)
    upload_id = await _pending(admin_user, uri, external_id="LIMS-FILE-9001", sample_ids=[s2.id, s1.id], size=len(DATA))

    f = await UploadService.complete_upload(session, admin_user.organization_id, upload_id, "")
    await session.commit()

    rows = await capture.deliveries()
    # Two distinct events; delivery order is not guaranteed.
    assert sorted(e for e, _ in rows) == [READY, REGISTERED]
    registered = next(d for e, d in rows if e == REGISTERED)
    ready = next(d for e, d in rows if e == READY)
    refs = sorted(
        [
            {
                "sample_id": s1.id,
                "external_id": "LIMS-SMP-1001",
                "experiment_id": exp.id,
                "experiment_external_id": "LIMS-EXP-042",
            },
            {
                "sample_id": s2.id,
                "external_id": None,
                "experiment_id": exp.id,
                "experiment_external_id": "LIMS-EXP-042",
            },
        ],
        key=lambda r: r["sample_id"],
    )
    assert registered == {
        "file_id": f.id,
        "external_id": "LIMS-FILE-9001",
        "filename": "reads.fastq.gz",
        "file_type": "fastq",
        "source_type": "upload",
        "project_id": None,
        "project_external_id": None,
        "experiment_id": None,
        "experiment_external_id": None,
        "sample_ids": [r["sample_id"] for r in refs],
        "samples": refs,
    }
    assert ready["file_id"] == f.id and ready["size_bytes"] == len(DATA) and ready["external_id"] == "LIMS-FILE-9001"
    # A client value is never stored as an observed checksum.
    assert (await session.get(File, f.id)).md5_checksum is None


async def test_an_asserted_md5_is_verified_by_the_worker_before_ready(session, admin_user, store, capture):
    uri = "s3://bioaf-ingest-t/uploads/u2/a.fastq.gz"
    await store.write_bytes(uri, DATA)
    upload_id = await _pending(admin_user, uri, expected_md5=MD5.upper())

    f = await UploadService.complete_upload(session, admin_user.organization_id, upload_id, MD5)
    await session.commit()
    assert [e for e, _ in await capture.deliveries()] == [REGISTERED]

    assert await file_verification_worker.run_once() is True
    assert sorted(e for e, _ in await capture.deliveries()) == [READY, REGISTERED]
    record = (await session.execute(select(FileVerification).where(FileVerification.file_id == f.id))).scalar_one()
    assert record.status == "verified" and record.expected_checksum == MD5 and record.observed_method == "streamed"


async def test_disagreeing_upload_assertions_register_nothing(session, admin_user, store, capture):
    uri = "s3://bioaf-ingest-t/uploads/u3/a.fastq.gz"
    await store.write_bytes(uri, DATA)
    upload_id = await _pending(admin_user, uri, expected_md5=MD5)
    with pytest.raises(ValidationError):
        await UploadService.complete_upload(session, admin_user.organization_id, upload_id, "0" * 32)
    await session.rollback()
    assert await capture.deliveries() == []
    assert (await session.execute(select(File))).scalars().all() == []


async def test_a_duplicate_upload_external_id_is_a_409(client, admin_token, session, admin_user, store, capture):
    uri = "s3://bioaf-ingest-t/uploads/u4/a.fastq.gz"
    await store.write_bytes(uri, DATA)
    first = await _pending(admin_user, uri, external_id="DUP")
    await UploadService.complete_upload(session, admin_user.organization_id, first, "")
    await session.commit()
    second = await _pending(admin_user, "s3://bioaf-ingest-t/uploads/u5/b.fastq.gz", external_id="DUP")
    with pytest.raises(ConflictError):
        await UploadService.complete_upload(session, admin_user.organization_id, second, "")


async def test_initiate_carries_the_external_id_to_completion(session, admin_user, store, capture, monkeypatch):
    async def bucket(_session):
        return "bioaf-ingest-t"

    monkeypatch.setattr(UploadService, "_get_ingest_bucket", staticmethod(bucket))

    from app.adapters.models import UploadSessionGrant, UploadSessionState, UploadSessionStatus

    async def begin(*a, **k):
        return UploadSessionGrant(protocol="gcs_resumable", handle="https://session.example/1", url="https://x")

    async def inspect(*a, **k):
        return UploadSessionStatus(state=UploadSessionState.COMPLETED)

    monkeypatch.setattr(store, "begin_upload_session", begin)
    monkeypatch.setattr(store, "inspect_upload_session", inspect)
    result = await UploadService.initiate_upload(
        session, admin_user.organization_id, admin_user.id, "x.fastq.gz", None, None, None, external_id="007"
    )
    await store.write_bytes(result["gcs_uri"], DATA)
    f = await UploadService.complete_upload(session, admin_user.organization_id, result["upload_id"], "")
    await session.commit()
    assert f.external_id == "007"
    [registered] = await capture.data(REGISTERED)
    assert registered["external_id"] == "007"


async def test_initiate_refuses_an_external_id_already_in_use(session, admin_user, store, monkeypatch):
    async def bucket(_session):
        return "bioaf-ingest-t"

    monkeypatch.setattr(UploadService, "_get_ingest_bucket", staticmethod(bucket))
    uri = "s3://bioaf-ingest-t/uploads/u6/a.fastq.gz"
    await store.write_bytes(uri, DATA)
    await UploadService.complete_upload(
        session, admin_user.organization_id, await _pending(admin_user, uri, external_id="X"), ""
    )
    await session.commit()
    with pytest.raises(ConflictError):
        await UploadService.initiate_upload(
            session, admin_user.organization_id, admin_user.id, "y.fastq.gz", None, None, None, external_id="X"
        )


async def test_simple_upload_registers_and_becomes_ready(session, admin_user, store, capture):
    f = await UploadService.simple_upload(
        session,
        admin_user.organization_id,
        admin_user.id,
        "notes.csv",
        io.BytesIO(b"a,b\n1,2\n"),
        size_bytes=8,
        external_id="CSV-1",
    )
    await session.commit()
    rows = dict(await capture.deliveries())
    assert sorted(rows) == [READY, REGISTERED]
    assert rows[REGISTERED]["external_id"] == "CSV-1" and rows[READY]["size_bytes"] == 8
    assert f.ready_at is not None


# --- pipeline outputs -------------------------------------------------------------------------


async def _run(session, admin_user, exp, *, pipeline="nf-core/rnaseq", samples=()):
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name=pipeline,
        status="completed",
        submitted_by_user_id=admin_user.id,
    )
    session.add(run)
    await session.flush()
    for s in samples:
        session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=s.id))
    await session.commit()
    return run


async def test_pipeline_outputs_announce_once_and_rescans_announce_nothing(session, admin_user, store, tree, capture):
    project, exp, s1, s2 = tree
    run = await _run(session, admin_user, exp, samples=[s1])
    uri = f"s3://bioaf-results-t/runs/{run.id}/star/LIMS-SMP-1001/LIMS-SMP-1001.bam"
    await store.write_bytes(uri, DATA)
    listing = [{"filename": "LIMS-SMP-1001.bam", "gcs_uri": uri, "size_bytes": len(DATA), "md5_hash": "a" * 32}]

    created = await PipelineOutputService.register_outputs(session, run, listing)
    await session.commit()
    again = await PipelineOutputService.register_outputs(session, run, listing)
    await session.commit()

    assert len(created) == 1 and again == []
    rows = await capture.deliveries()
    assert sorted(e for e, _ in rows) == [READY, REGISTERED]
    data = next(d for e, d in rows if e == REGISTERED)
    assert data["experiment_id"] == exp.id and data["project_id"] == project.id
    assert data["project_external_id"] == "PRJ-1" and data["sample_ids"] == [s1.id]
    # A listing's provider hint is not an independent observation.
    assert (await session.get(File, created[0].id)).md5_checksum is None


async def test_an_existing_unready_output_is_made_ready_without_reannouncing(session, admin_user, store, tree, capture):
    _, exp, _, _ = tree
    run = await _run(session, admin_user, exp)
    uri = f"s3://bioaf-results-t/runs/{run.id}/multiqc/report.html"
    legacy = File(
        organization_id=admin_user.organization_id,
        storage_uri=uri,
        filename="report.html",
        file_type="report",
        source_type="pipeline_output",
        source_pipeline_run_id=run.id,
        source_storage_uri=uri,
    )
    session.add(legacy)
    await session.commit()
    await store.write_bytes(uri, DATA)

    await PipelineOutputService.register_outputs(
        session, run, [{"filename": "report.html", "gcs_uri": uri, "size_bytes": len(DATA)}]
    )
    await session.commit()
    assert [e for e, _ in await capture.deliveries()] == [READY]


async def test_ambiguous_legacy_outputs_register_nothing_more(session, admin_user, store, tree, capture):
    _, exp, _, _ = tree
    run = await _run(session, admin_user, exp)
    uri = f"s3://bioaf-results-t/runs/{run.id}/dup.txt"
    for _ in range(2):
        session.add(
            File(
                organization_id=admin_user.organization_id,
                storage_uri=uri,
                filename="dup.txt",
                file_type="other",
                source_type="pipeline_output",
                source_pipeline_run_id=run.id,
            )
        )
    await session.commit()
    await store.write_bytes(uri, DATA)

    created = await PipelineOutputService.register_outputs(session, run, [{"filename": "dup.txt", "gcs_uri": uri}])
    await session.commit()
    assert created == []
    count = len((await session.execute(select(File).where(File.storage_uri == uri))).scalars().all())
    assert count == 2
    assert await capture.deliveries() == []


async def test_fetchngs_fastq_waits_for_its_samplesheet_before_readiness(session, admin_user, store, tree, capture):
    _, exp, _, _ = tree
    run = await _run(session, admin_user, exp, pipeline="nf-core/fetchngs")
    out = f"s3://bioaf-results-t/runs/{run.id}"
    uri = f"{out}/fastq/SRR1_1.fastq.gz"
    await store.write_bytes(uri, DATA)
    await store.write_text(
        f"{out}/samplesheet/samplesheet.csv",
        f"sample,run_accession,fastq_1,fastq_2,md5_1\nS1,SRR1,/w/SRR1_1.fastq.gz,,{MD5}\n",
    )

    [f] = await PipelineOutputService.register_outputs(session, run, [{"filename": "SRR1_1.fastq.gz", "gcs_uri": uri}])
    await session.commit()
    assert [e for e, _ in await capture.deliveries()] == [REGISTERED]
    record = (await session.execute(select(FileVerification).where(FileVerification.file_id == f.id))).scalar_one()
    assert record.status == "waiting_expectation"

    while await file_verification_worker.run_once():
        pass
    assert sorted(e for e, _ in await capture.deliveries()) == [READY, REGISTERED]
    await session.refresh(record)
    assert record.status == "verified" and record.expectation_source == "fetchngs_csv"
