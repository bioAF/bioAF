"""Collecting a run's outputs: discovery under its one authorized location, returning readable URIs.

The monitor and the registration CLI pass the run's output URI to ``collect_outputs``. The local
GCS/S3 modes and NFS used to treat that argument as a filesystem directory, ``os.path.isdir`` of a
``gs://`` string is False, and nothing was ever collected; the cloud modes listed it as a bare
prefix, so run 1 also collected runs 10 and 100. And every consumer trusted a stored ``outdir``.

These run the real local adapters on a temporary root and read back every returned URI, and the
consumer tests use real database rows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.settings_contract import ComputeMode
from app.adapters.storage import gcs as gcs_module
from app.adapters.storage import s3 as s3_module
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ValidationError
from app.models.component import PlatformConfig
from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun

OUTPUTS = {
    "multiqc/multiqc_report.html": b"<html>report</html>",
    "salmon/quant.sf": bytes(range(256)) * 4,
    "pipeline_info/empty.txt": b"",
    "top.txt": "üñí\n".encode(),
}


@pytest.fixture
def local_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    monkeypatch.setattr(gcs_module, "LOCAL_DATA_ROOT", str(tmp_path / "gcs"))
    monkeypatch.setattr(s3_module, "LOCAL_DATA_ROOT", str(tmp_path / "s3"))
    return tmp_path


def _adapters(root):
    return {
        "gcs": GcsStorageProvider(org_slug="t"),
        "s3": S3StorageProvider(org_slug="t"),
        "nfs": NfsStorageProvider(root=str(root / "nfs")),
    }


async def _seed(storage, bucket: str, run_key: str) -> str:
    out = storage.build_uri(bucket, run_key)
    for rel, data in OUTPUTS.items():
        await storage.write_bytes(f"{out}/{rel}", data)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["gcs", "s3", "nfs"])
async def test_collection_returns_every_object_under_the_run_and_they_read_back(local_root, name):
    storage = _adapters(local_root)[name]
    out = await _seed(storage, "results", "experiments/3/pipeline-runs/1")
    # Neighbours whose keys start with the same run number.
    for neighbour in ("10", "100", "11"):
        await storage.write_bytes(storage.build_uri("results", f"experiments/3/pipeline-runs/{neighbour}/x"), b"no")

    collected = await storage.collect_outputs(out, {"id": 1, "experiment_id": 3})

    by_uri = {o.storage_uri: o for o in collected}
    assert set(by_uri) == {f"{out}/{rel}" for rel in OUTPUTS}
    for rel, data in OUTPUTS.items():
        obj = by_uri[f"{out}/{rel}"]
        assert obj.filename == rel.rsplit("/", 1)[-1]
        assert obj.size_bytes == len(data)
        assert await storage.read_bytes(obj.storage_uri) == data


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["gcs", "s3", "nfs"])
async def test_collection_from_a_filesystem_path_is_refused(local_root, name):
    storage = _adapters(local_root)[name]
    work = local_root / "work"
    work.mkdir()
    (work / "result.txt").write_text("x")
    with pytest.raises(ValidationError):
        await storage.collect_outputs(str(work), {"id": 1, "experiment_id": 3})


@pytest.mark.asyncio
async def test_cloud_gcs_collection_lists_the_separator_terminated_prefix_with_credentials(mode_adapter_factory):
    storage = mode_adapter_factory(GcsStorageProvider, org_slug="t", mode=ComputeMode.KUBERNETES)
    storage._credentials, storage._credentials_loaded = "CREDS", True
    blob = MagicMock(size=3, md5_hash="m", time_created=None, updated=None, generation=1)
    blob.name = "experiments/3/pipeline-runs/1/a.txt"
    client = MagicMock()
    client.bucket.return_value.list_blobs.return_value = [blob]
    with patch.object(storage, "_get_gcs_client", return_value=client) as get_client:
        collected = await storage.collect_outputs("gs://res/experiments/3/pipeline-runs/1", {"id": 1})
    get_client.assert_called_with("CREDS")
    client.bucket.return_value.list_blobs.assert_called_once_with(prefix="experiments/3/pipeline-runs/1/")
    assert [o.storage_uri for o in collected] == ["gs://res/experiments/3/pipeline-runs/1/a.txt"]


# --- consumers ------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def run(session, admin_user):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", owner_user_id=admin_user.id)
    session.add(exp)
    await session.flush()
    session.add(PlatformConfig(key="results_bucket_name", value="results"))
    r = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        status="completed",
    )
    session.add(r)
    await session.flush()
    await session.commit()
    return r


async def _registered(session, run_id):
    return list((await session.execute(select(File).where(File.source_pipeline_run_id == run_id))).scalars().all())


@pytest.mark.asyncio
async def test_the_cli_registers_the_runs_own_outputs_with_readable_uris(session, run, local_root):
    from app.cli.register_outputs import register_outputs_for_run

    storage = GcsStorageProvider(org_slug="t")
    out = await _seed(storage, "results", f"experiments/{run.experiment_id}/pipeline-runs/{run.id}")
    await storage.write_bytes(
        storage.build_uri("results", f"experiments/{run.experiment_id}/pipeline-runs/{run.id}0/x"), b"neighbour"
    )
    with patch("app.cli.register_outputs.get_storage_adapter", return_value=storage):
        count = await register_outputs_for_run(session, run)
    await session.commit()

    files = await _registered(session, run.id)
    assert count == len(OUTPUTS) == len(files)
    for f in files:
        assert f.storage_uri.startswith(out + "/")
        assert await storage.read_bytes(f.storage_uri) == OUTPUTS[f.storage_uri[len(out) + 1 :]]


@pytest.mark.asyncio
@pytest.mark.parametrize("outdir", ["gs://foreign/", "gs://results/experiments/{e}/pipeline-runs/{other}"])
async def test_the_cli_refuses_a_stored_foreign_outdir_before_reading(session, run, local_root, outdir):
    from app.cli.register_outputs import register_outputs_for_run

    storage = GcsStorageProvider(org_slug="t")
    foreign = outdir.format(e=run.experiment_id, other=run.id + 1)
    await storage.write_bytes(foreign.rstrip("/") + "/secret.txt", b"theirs")
    run.parameters_json = {"outdir": foreign}
    spy = MagicMock(wraps=storage)
    spy.collect_outputs = AsyncMock(side_effect=storage.collect_outputs)
    with patch("app.cli.register_outputs.get_storage_adapter", return_value=spy):
        count = await register_outputs_for_run(session, run)
    assert count == 0
    spy.collect_outputs.assert_not_awaited()
    assert await _registered(session, run.id) == []


@pytest.mark.asyncio
async def test_the_monitor_refuses_a_stored_foreign_outdir(session, run, local_root):
    from app.services.pipeline_monitor_service import PipelineMonitorService

    storage = GcsStorageProvider(org_slug="t")
    await storage.write_bytes("gs://foreign/secret.txt", b"theirs")
    await _seed(storage, "results", f"experiments/{run.experiment_id}/pipeline-runs/{run.id}")
    run.parameters_json = {"outdir": "gs://foreign/"}
    with (
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=storage),
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=AsyncMock()),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService._handle_completion(session, run)
    assert await _registered(session, run.id) == []


@pytest.mark.asyncio
async def test_the_monitor_collects_the_runs_own_outputs(session, run, local_root):
    from app.services.pipeline_monitor_service import PipelineMonitorService

    storage = GcsStorageProvider(org_slug="t")
    out = await _seed(storage, "results", f"experiments/{run.experiment_id}/pipeline-runs/{run.id}")
    with (
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=storage),
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=AsyncMock()),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService._handle_completion(session, run)
    files = await _registered(session, run.id)
    assert {f.storage_uri for f in files} == {f"{out}/{rel}" for rel in OUTPUTS}


@pytest.mark.asyncio
async def test_validation_ingest_refuses_a_stored_foreign_outdir(session, run, local_root):
    """The driver's fetchngs ingest reads the samplesheet from the run's location, never a stored one."""
    from app.services import validation_driver_service as driver

    run.pipeline_name = "nf-core/fetchngs"
    run.parameters_json = {"outdir": "gs://foreign/"}
    storage = GcsStorageProvider(org_slug="t")
    with patch.object(driver, "get_storage_adapter", return_value=storage):
        with pytest.raises(ValidationError):
            await driver._resolve_outdir(session, run)


@pytest.mark.asyncio
async def test_fetchngs_never_registers_a_fastq_outside_the_runs_location(session, run, local_root):
    from app.services.fetchngs_ingest_service import _fastq_storage_uri

    out = f"gs://results/experiments/{run.experiment_id}/pipeline-runs/{run.id}"
    assert _fastq_storage_uri(out, "/work/ab/SRR1_1.fastq.gz") == f"{out}/fastq/SRR1_1.fastq.gz"
    for hostile in ("..", "/work/..", ".", "", "/work/"):
        assert _fastq_storage_uri(out, hostile) is None
