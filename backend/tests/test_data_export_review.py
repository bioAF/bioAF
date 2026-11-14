"""Regressions from the owner's review of the plan 24 branch (2026-11-14), one per finding."""

from __future__ import annotations

import asyncio
import io
import os
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.adapters import registry
from app.adapters.models import SignedDownload, StorageStore
from app.services.data_export import resources

UTC = timezone.utc


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.adapters.storage import gcs
    from app.config import settings

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "export_scratch_dir", str(tmp_path / "scratch"))
    monkeypatch.setattr(
        resources,
        "disk_probe",
        lambda path: lambda: resources.DiskSpace(free=500 * resources.GiB, total=1000 * resources.GiB),
    )
    return registry.get_storage_adapter()


@pytest.fixture(autouse=True)
def _slot_is_free_after_each_test():
    yield
    assert not resources.SLOT.busy


@pytest.fixture
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


async def _seed(session, org: int, *, samples: int = 0) -> None:
    await session.execute(
        text("INSERT INTO projects (id, organization_id, name, status) VALUES (1, :o, 'P', 'active')"), {"o": org}
    )
    await session.execute(
        text("INSERT INTO experiments (id, organization_id, project_id, name, status) VALUES (1, :o, 1, 'E', 'x')"),
        {"o": org},
    )
    for i in range(samples):
        await session.execute(
            text(
                "INSERT INTO samples (experiment_id, external_id, status, prep_notes) VALUES (1, :e, 'registered', :n)"
            ),
            {"e": f"S{i:05d}", "n": "n" * 200},
        )
    await session.commit()


def _post(client, headers, method="direct"):
    return client.post(
        "/api/experiments/1/export/data",
        json={"delivery_method": method, "include_fastq": False, "include_provenance": False},
        headers=headers,
    )


async def _until(predicate, seconds: float = 5) -> None:
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    assert predicate()


# -- 1. A timed-out upload keeps ownership until its worker settles ------------------------------


@pytest.mark.asyncio
async def test_a_timed_out_upload_keeps_scratch_and_slot_and_removes_its_late_object(store, tmp_path, monkeypatch):
    from app.services import export_delivery
    from app.services.data_export.archive import ArchivePlan, ExportJob
    from app.services.export_service import PreparedExport

    monkeypatch.setattr(resources, "ISSUE_DEADLINE", 0.2)
    monkeypatch.setattr(resources, "SETTLE_SECONDS", 0.1)
    release = threading.Event()
    real_upload = store.upload_file
    uploaded: list[str] = []

    async def hanging_upload(uri, file_obj, *, content_type=None):
        data = file_obj.read()  # the SDK already holds the bytes; its network write then stalls

        def blocked():
            release.wait(10)
            return data

        data = await asyncio.to_thread(blocked)
        await real_upload(uri, io.BytesIO(data), content_type=content_type)
        uploaded.append(uri)

    monkeypatch.setattr(store, "upload_file", hanging_upload)
    job = ExportJob.admit([1], scratch_dir=str(tmp_path / "scratch"))
    plan = ArchivePlan()
    plan.add_generated("r/README.txt", "x")
    size = await job.build(plan, store)
    prepared = PreparedExport(job=job, size=size, filename="x.zip")

    with pytest.raises(Exception):
        await export_delivery.issue_signed_link(prepared, org_id=7)
    prepared.release()  # what the route does on any failure

    assert resources.SLOT.busy and job.workdir.exists()  # the worker still owns them
    release.set()
    await _until(lambda: not resources.SLOT.busy)
    assert not job.workdir.exists()
    exports = await store.list_objects(await store.resolve_uri(StorageStore.CONFIG_BACKUPS, "exports/"))
    assert uploaded and exports == []


# -- 2. Manifests are built after admission, within the generated allowance ----------------------


@pytest.mark.asyncio
async def test_a_busy_export_reads_no_samples(client, session, admin_user, headers, store, monkeypatch):
    from app.services.data_export import selection

    calls = []

    async def counted(*a, **k):
        calls.append(1)
        return b""

    monkeypatch.setattr(selection, "write_sample_manifest", counted)
    await _seed(session, admin_user.organization_id, samples=3)
    calls.clear()

    lease = resources.SLOT.try_acquire()
    try:
        resp = await _post(client, headers)
    finally:
        lease.release()

    assert resp.status_code == 429
    assert calls == []


@pytest.mark.asyncio
async def test_a_manifest_over_the_generated_allowance_is_refused_while_it_is_read(
    client, session, admin_user, headers, store, monkeypatch
):
    monkeypatch.setattr(resources, "GENERATED_ALLOWANCE", 20_000)
    await _seed(session, admin_user.organization_id, samples=400)  # ~90 KB of manifest

    resp = await _post(client, headers)

    assert resp.status_code == 413
    assert resp.json()["code"] == "export_limit_exceeded"


@pytest.mark.asyncio
async def test_manifests_still_carry_every_sample(client, session, admin_user, headers, store):
    await _seed(session, admin_user.organization_id, samples=1200)

    resp = await _post(client, headers)

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    [manifest] = [n for n in zf.namelist() if n.endswith("sample_manifest.csv")]
    rows = zf.read(manifest).decode().strip().splitlines()
    assert len(rows) == 1201
    assert rows[1].startswith("S00000,")


# -- 3. AWS credential discovery runs off the event loop -----------------------------------------


@pytest.mark.parametrize("call", ["check", "sign"])
@pytest.mark.asyncio
async def test_slow_credential_discovery_does_not_block_the_loop(mode_adapter_factory, monkeypatch, call):
    from botocore.credentials import Credentials

    from app.adapters.storage.s3 import S3StorageProvider
    from app.settings_contract import ComputeMode

    adapter = mode_adapter_factory(S3StorageProvider, mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"

    async def none(*a, **k):
        return None

    monkeypatch.setattr(adapter, "_get_credentials", none)

    def slow_credentials():
        time.sleep(0.15)  # an IMDS or SSO round trip
        return Credentials("AKIASLOW", "secret")

    monkeypatch.setattr(adapter, "_signing_credentials", slow_credentials)
    delays = []

    async def heartbeat():
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            delays.append(time.perf_counter() - t - 0.01)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.03)
    if call == "check":
        await adapter.check_signed_download(min_ttl_seconds=3600)
    else:
        await adapter.sign_download("s3://b/k", max_ttl_seconds=86400, min_ttl_seconds=3600)
    await asyncio.sleep(0.05)  # let the heartbeat record the iteration that was running during the call
    beat.cancel()

    assert max(delays) < 0.1


# -- 4. Unknown-size sources cannot exceed the source ceiling on disk ----------------------------


@pytest.mark.asyncio
async def test_an_unknown_size_object_is_stopped_at_the_source_ceiling(
    client, session, admin_user, headers, store, monkeypatch
):
    monkeypatch.setattr(resources, "MAX_SOURCE_BYTES", 100_000)
    seen = {"max_extent": 0}
    real_write = resources.BoundedFile.write

    def write(self, data):
        result = real_write(self, data)
        if self.path.name.startswith("source-"):
            seen["max_extent"] = max(seen["max_extent"], self.extent)
        return result

    monkeypatch.setattr(resources.BoundedFile, "write", write)
    uri = store.build_uri("bioaf-raw-demo", "big.bin")
    await store.write_bytes(uri, os.urandom(400_000))
    await _seed(session, admin_user.organization_id)
    await session.execute(
        text(
            "INSERT INTO files (organization_id, filename, gcs_uri, storage_uri, file_type, size_bytes, experiment_id,"
            " source_type) VALUES (:o, 'big.bin', :u, :u, 'bin', NULL, 1, 'upload')"
        ),
        {"o": admin_user.organization_id, "u": uri},
    )
    await session.commit()

    resp = await _post(client, headers)

    assert resp.status_code == 413
    assert seen["max_extent"] <= 100_000


# -- 5. The audit commit cannot outlast the link's minimum lifetime ------------------------------


@pytest.mark.parametrize("commit_delay", [0.0, 0.6])
@pytest.mark.asyncio
async def test_a_slow_audit_commit_never_hands_over_a_link_below_the_minimum(
    client, session, admin_user, headers, store, monkeypatch, commit_delay
):
    from app.api import data_export
    from app.config import settings
    from app.services import export_delivery

    monkeypatch.setattr(settings, "export_signed_min_ttl_seconds", 1)
    monkeypatch.setattr(export_delivery, "HANDOFF_SECONDS", 0.3)

    async def sign(uri, *, max_ttl_seconds, min_ttl_seconds):
        return SignedDownload(url="https://signed.test/x", expires_at=datetime.now(UTC) + timedelta(seconds=1.5))

    monkeypatch.setattr(store, "sign_download", sign)
    real_audit = data_export.log_action

    async def slow_audit(**kwargs):
        await asyncio.sleep(commit_delay)
        await real_audit(**kwargs)

    monkeypatch.setattr(data_export, "log_action", slow_audit)
    await _seed(session, admin_user.organization_id)

    resp = await _post(client, headers, method="gcs")

    if commit_delay:
        assert resp.status_code != 200
        exports = await store.list_objects(await store.resolve_uri(StorageStore.CONFIG_BACKUPS, "exports/"))
        assert exports == []
    else:
        assert resp.status_code == 200
        advertised = resp.json()["expires_in_hours"] * 3600
        assert advertised <= 1.5
        assert advertised >= 0


# -- 6. A lower minimum never trusts botocore's synthetic IMDS expiry ----------------------------


def test_a_fifteen_minute_minimum_refuses_imds_extended_credentials(monkeypatch):
    from botocore.credentials import RefreshableCredentials
    from botocore.utils import InstanceMetadataFetcher

    from app.adapters.storage import s3_signing
    from app.exceptions import StorageUnavailableError

    import botocore.utils

    monkeypatch.setattr(botocore.utils.random, "randint", lambda a, b: b)  # the longest jitter: +20 min
    raw = (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"access_key": "AKIA", "secret_key": "s", "token": "t", "expiry_time": raw}
    InstanceMetadataFetcher()._evaluate_expiration(data)
    assert datetime.fromisoformat(data["expiry_time"].replace("Z", "+00:00")) > datetime.now(UTC) + timedelta(
        minutes=19
    )
    creds = RefreshableCredentials(
        access_key="AKIA",
        secret_key="s",
        token="t",
        expiry_time=datetime.now(UTC),
        refresh_using=lambda: dict(data),
        method="iam-role",
    )
    snapshot_or_refusal = None
    try:
        snap = s3_signing.signing_snapshot(creds, required_seconds=15 * 60 + 120)
        snapshot_or_refusal = s3_signing.link_lifetime(snap, max_ttl_seconds=86400, min_ttl_seconds=15 * 60)
    except StorageUnavailableError:
        snapshot_or_refusal = "refused"

    assert snapshot_or_refusal == "refused"


def test_a_fifteen_minute_minimum_still_signs_genuinely_valid_credentials():
    from botocore.credentials import RefreshableCredentials

    from app.adapters.storage import s3_signing

    creds = RefreshableCredentials(
        access_key="AKIA",
        secret_key="s",
        token="t",
        expiry_time=datetime.now(UTC) + timedelta(minutes=40),
        refresh_using=lambda: None,
        method="iam-role",
    )
    snap = s3_signing.signing_snapshot(creds, required_seconds=15 * 60 + 120)

    assert s3_signing.link_lifetime(snap, max_ttl_seconds=86400, min_ttl_seconds=15 * 60) > 30 * 60


# -- 7. Delivered exports leave no backstop timers -----------------------------------------------


def _backstops() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "bioaf:data-export-backstop" and not t.done()]


@pytest.mark.asyncio
async def test_a_delivered_export_leaves_no_backstop_timer(client, session, admin_user, headers, store):
    await _seed(session, admin_user.organization_id)

    for _ in range(3):
        assert (await _post(client, headers)).status_code == 200
    await asyncio.sleep(0)

    assert _backstops() == []
