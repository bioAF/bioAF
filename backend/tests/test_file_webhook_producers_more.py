"""01C.2: fetchngs attachment, session/notebook outputs, QC plots, and the plot scanner.

Each new File is announced once with its links; an adopted or rediscovered File is never announced
again. fetchngs CSV checksums are expectations verified against the stored bytes. The plot scanner
registers an object only under the organization that owns its experiment/run, never the first
organization it happens to visit.
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.storage.s3 import S3StorageProvider
from app.models.experiment import Experiment
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.notebook_session import ComputeSession
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.services import file_verification_worker
from app.services.fetchngs_ingest_service import FetchngsIngestService
from app.services.pipeline_output_service import PipelineOutputService
from tests.support.webhook_capture import WebhookCapture

pytestmark = pytest.mark.asyncio

REGISTERED, READY = "file.registered", "file.ready"
DATA = b"@r\nACGT\n+\nIIII\n" * 64
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


async def _events(capture):
    return sorted(e for e, _ in await capture.deliveries())


# --- fetchngs attachment ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def fetch(session, admin_user, store):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", external_id="EXP", status="registered")
    session.add(exp)
    await session.flush()
    sample = Sample(experiment_id=exp.id, external_id="S1", status="registered")
    session.add(sample)
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/fetchngs",
        status="completed",
        submitted_by_user_id=admin_user.id,
    )
    session.add(run)
    await session.commit()
    out = f"s3://bioaf-results-t/runs/{run.id}"
    await store.write_bytes(f"{out}/fastq/SRR1_1.fastq.gz", DATA)
    await store.write_bytes(f"{out}/fastq/SRR1_2.fastq.gz", DATA[::-1])
    await store.write_text(
        f"{out}/samplesheet/samplesheet.csv",
        f"sample,run_accession,fastq_1,fastq_2,md5_1,md5_2\nS1,SRR1,/w/SRR1_1.fastq.gz,/w/SRR1_2.fastq.gz,{MD5},\n",
    )
    return run, sample, out


async def test_attachment_registers_once_and_verifies_csv_checksums(session, store, fetch, capture):
    run, sample, out = fetch
    created = await FetchngsIngestService.attach_fastq_files(session, run, outdir=out, storage_adapter=store)
    await session.commit()
    assert len(created) == 2
    by_name = {f.filename: f for f in created}

    # The mate without a CSV checksum is ready at once; the other waits for its digest.
    assert await _events(capture) == [READY, REGISTERED, REGISTERED]
    registered = await capture.data(REGISTERED)
    assert all(d["sample_ids"] == [sample.id] for d in registered)

    while await file_verification_worker.run_once():
        pass
    assert await _events(capture) == [READY, READY, REGISTERED, REGISTERED]
    record = (
        await session.execute(select(FileVerification).where(FileVerification.file_id == by_name["SRR1_1.fastq.gz"].id))
    ).scalar_one()
    assert record.status == "verified" and record.expected_checksum == MD5
    # The CSV value was never written as an observed checksum before it was verified.
    assert (await session.get(File, by_name["SRR1_2.fastq.gz"].id)).md5_checksum is None

    # Re-attaching changes nothing and announces nothing.
    again = await FetchngsIngestService.attach_fastq_files(session, run, outdir=out, storage_adapter=store)
    await session.commit()
    assert again == []
    assert len(await capture.deliveries()) == 4


async def test_attachment_adopts_rows_the_monitor_registered(session, store, fetch, capture):
    run, sample, out = fetch
    listing = [
        {"filename": "SRR1_1.fastq.gz", "gcs_uri": f"{out}/fastq/SRR1_1.fastq.gz"},
        {"filename": "SRR1_2.fastq.gz", "gcs_uri": f"{out}/fastq/SRR1_2.fastq.gz"},
    ]
    generic = await PipelineOutputService.register_outputs(session, run, listing)
    await session.commit()
    assert await _events(capture) == [REGISTERED, REGISTERED]  # both wait for their samplesheet

    created = await FetchngsIngestService.attach_fastq_files(session, run, outdir=out, storage_adapter=store)
    await session.commit()
    assert created == []
    while await file_verification_worker.run_once():
        pass
    events = await capture.deliveries()
    assert sorted(e for e, _ in events) == [READY, READY, REGISTERED, REGISTERED]
    assert {d["file_id"] for e, d in events if e == READY} == {f.id for f in generic}


async def test_attachment_after_relocation_reuses_the_same_file(session, store, fetch, capture):
    run, sample, out = fetch
    [f1, f2] = await FetchngsIngestService.attach_fastq_files(session, run, outdir=out, storage_adapter=store)
    await session.commit()
    # Canonical bytes moved (as an NFS protected copy does); the original stays in place.
    f1.storage_uri = "s3://bioaf-results-t/verified-files/x/payload"
    await session.commit()
    before = len(await capture.deliveries())
    assert await FetchngsIngestService.attach_fastq_files(session, run, outdir=out, storage_adapter=store) == []
    await session.commit()
    files = (await session.execute(select(File).where(File.source_pipeline_run_id == run.id))).scalars().all()
    assert len(files) == 2
    assert len(await capture.deliveries()) == before


# --- session and notebook outputs -------------------------------------------------------------


@pytest_asyncio.fixture
async def compute(session, admin_user):
    exp = Experiment(organization_id=admin_user.organization_id, name="NB", external_id="NB-EXP", status="analysis")
    session.add(exp)
    await session.flush()
    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        experiment_id=exp.id,
        status="running",
    )
    session.add(cs)
    await session.commit()
    return cs, exp


async def test_session_outputs_are_registered_and_ready(session, admin_user, store, compute, capture):
    from app.services.session_output_service import SessionOutputService

    cs, exp = compute
    uri = f"s3://bioaf-working-t/sessions/{cs.id}/outputs/analysis.h5ad"
    await store.write_bytes(uri, DATA)
    count = await SessionOutputService.register_outputs(
        session,
        cs.id,
        admin_user.organization_id,
        None,
        exp.id,
        admin_user.id,
        [
            {"filename": "analysis.h5ad", "gcs_uri": uri, "size_bytes": len(DATA)},
            {"filename": ".bashrc", "gcs_uri": "x"},
        ],
    )
    await session.commit()
    assert count == 1
    events = await capture.deliveries()
    assert sorted(e for e, _ in events) == [READY, REGISTERED]
    registered = next(d for e, d in events if e == REGISTERED)
    assert registered["source_type"] == "notebook_output" and registered["experiment_external_id"] == "NB-EXP"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_notebook_registration_accepts_an_external_id_inside_the_session_namespace(
    client, admin_token, session, admin_user, store, compute, capture
):
    cs, _ = compute
    uri = f"s3://bioaf-working-t/sessions/{cs.id}/outputs/fig.png"
    await store.write_bytes(uri, DATA)
    r = await client.post(
        f"/api/v1/notebooks/sessions/{cs.id}/register-outputs",
        headers=_auth(admin_token),
        json={
            "outputs": [
                {"filename": "fig.png", "size_bytes": len(DATA), "gcs_uri": uri, "external_id": "LIMS-FIG-1"},
                {"filename": "probe.png", "size_bytes": 1, "gcs_uri": "s3://someone-elses-bucket/secret.png"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["registered_count"] == 1
    [registered] = await capture.data(REGISTERED)
    assert registered["external_id"] == "LIMS-FIG-1"
    names = (await session.execute(select(File.filename))).scalars().all()
    assert names == ["fig.png"]


async def test_notebook_registration_is_scoped_to_the_callers_organization(
    client, admin_token, session, store, capture
):
    other = Organization(name="Other")
    session.add(other)
    await session.flush()
    from app.models.user import User

    owner = (await session.execute(select(User).limit(1))).scalar_one()
    cs = ComputeSession(
        user_id=owner.id,
        organization_id=other.id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=1,
        memory_gb=1,
        status="running",
    )
    session.add(cs)
    await session.commit()
    r = await client.post(
        f"/api/v1/notebooks/sessions/{cs.id}/register-outputs",
        headers=_auth(admin_token),
        json={"outputs": [{"filename": "a.png", "gcs_uri": f"s3://bioaf-working-t/sessions/{cs.id}/outputs/a.png"}]},
    )
    assert r.status_code == 404
    assert (await session.execute(select(File))).scalars().all() == []


# --- QC plots and the plot scanner ------------------------------------------------------------


async def test_qc_plot_collection_announces_registration_and_readiness(
    session, admin_user, store, capture, monkeypatch
):
    from app.services.qc_dashboard_service import QCDashboardService

    exp = Experiment(organization_id=admin_user.organization_id, name="Q", status="analysis")
    session.add(exp)
    await session.flush()
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/rnaseq",
        status="completed",
    )
    session.add(run)
    await session.commit()

    async def bucket(_session):
        return "bioaf-results-t"

    monkeypatch.setattr("app.services.qc_dashboard_service.get_results_bucket", bucket)

    class Template:
        MULTIQC_PLOTS = [("plot.png", "A plot", "fastqc")]

    monkeypatch.setattr("app.services.qc_dashboard_service.get_template", lambda name: Template)
    uri = f"s3://bioaf-results-t/experiments/{exp.id}/pipeline-runs/{run.id}/multiqc/plot.png"
    await store.write_bytes(uri, DATA)
    plots = await QCDashboardService._collect_plots(session, admin_user.organization_id, run, template_name="x")
    await session.commit()
    assert len(plots) == 1
    assert await _events(capture) == [READY, REGISTERED]


async def test_the_plot_scanner_registers_objects_under_their_owning_organization(
    session, admin_user, store, capture, monkeypatch
):
    from app.services.plot_archive_service import PlotArchiveService

    # An earlier organization that does not own the object must not get it.
    first = Organization(name="AAA first")
    session.add(first)
    await session.flush()
    exp = Experiment(organization_id=admin_user.organization_id, name="Mine", status="analysis")
    foreign = Experiment(organization_id=first.id, name="Theirs", status="analysis")
    session.add_all([exp, foreign])
    await session.flush()
    run = PipelineRun(organization_id=first.id, experiment_id=foreign.id, pipeline_name="p", status="completed")
    session.add(run)
    await session.commit()

    async def bucket(_session, key):
        return "bioaf-results-t"

    monkeypatch.setattr("app.services.plot_archive_service.PlatformConfigService.get", bucket)
    owned = f"s3://bioaf-results-t/experiments/{exp.id}/plots/a.png"
    unscoped = "s3://bioaf-results-t/loose/b.png"
    conflicting = f"s3://bioaf-results-t/experiments/{exp.id}/pipeline-runs/{run.id}/c.png"
    for uri in (owned, unscoped, conflicting):
        await store.write_bytes(uri, DATA)

    await PlotArchiveService.scan_and_index(session)

    rows = (await session.execute(select(File.storage_uri, File.organization_id))).all()
    assert rows == [(owned, admin_user.organization_id)]
    assert await _events(capture) == [READY, REGISTERED]
