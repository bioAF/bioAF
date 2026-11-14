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


# -- The cancellable archive reader through the locked S3 upload path ----------------------------


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_the_archive_reader_uploads_through_s3transfer_and_stops_when_cancelled(
    mode_adapter_factory, monkeypatch, tmp_path, cancel
):
    import boto3

    from app.adapters.storage.s3 import S3StorageProvider
    from app.services.data_export.archive import CancellableReader
    from app.services.data_export.resources import ExportCancelled, ScratchBudget
    from app.settings_contract import ComputeMode

    adapter = mode_adapter_factory(S3StorageProvider, mode=ComputeMode.KUBERNETES)

    async def none(*a, **k):
        return None

    monkeypatch.setattr(adapter, "_get_credentials", none)
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    received = {}

    def put_object(**kwargs):
        received["body"] = kwargs["Body"].read()
        return {"ETag": '"e"'}

    client.put_object = put_object
    monkeypatch.setattr(adapter, "_get_s3_client", lambda creds=None: client)
    archive = tmp_path / "export.zip"
    payload = os.urandom(300_000)
    archive.write_bytes(payload)
    budget = ScratchBudget(10, probe=lambda: resources.DiskSpace(free=10**12, total=10**12))
    if cancel:
        budget.cancel()

    with open(archive, "rb") as raw:
        if cancel:
            with pytest.raises(ExportCancelled):
                await adapter.upload_file("s3://b/exports/1/x.zip", CancellableReader(raw, budget))
        else:
            await adapter.upload_file("s3://b/exports/1/x.zip", CancellableReader(raw, budget))

    assert received.get("body") == (None if cancel else payload)


# == Follow-up review (2026-11-14) ===============================================================


def _resolver_extended_expiry(monkeypatch, raw_seconds: int) -> dict:
    """Credential data as the default resolver's own IMDS fetcher would stretch it (longest jitter)."""
    import botocore.credentials
    import botocore.session
    import botocore.utils

    monkeypatch.setattr(botocore.utils.random, "randint", lambda a, b: b)
    resolver = botocore.credentials.create_credential_resolver(botocore.session.get_session())
    fetcher = resolver.get_provider("iam-role")._role_fetcher
    raw = (datetime.now(UTC) + timedelta(seconds=raw_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"access_key": "AKIA", "secret_key": "s", "token": "t", "expiry_time": raw}
    fetcher._evaluate_expiration(data)
    return data


def test_the_ceiling_covers_the_resolvers_real_extension(monkeypatch):
    from app.adapters.storage import s3_signing

    data = _resolver_extended_expiry(monkeypatch, 300)
    extended = (datetime.fromisoformat(data["expiry_time"].replace("Z", "+00:00")) - datetime.now(UTC)).total_seconds()

    assert extended > 24 * 60  # the resolver's window is 15 min, plus up to 10 min of jitter
    assert s3_signing.EXTENSION_CEILING_SECONDS >= extended


def test_a_fifteen_minute_minimum_refuses_the_resolvers_extended_credentials(monkeypatch):
    from botocore.credentials import RefreshableCredentials

    from app.adapters.storage import s3_signing
    from app.exceptions import StorageUnavailableError

    data = _resolver_extended_expiry(monkeypatch, 300)
    creds = RefreshableCredentials(
        access_key="AKIA",
        secret_key="s",
        token="t",
        expiry_time=datetime.now(UTC),
        refresh_using=lambda: dict(data),
        method="iam-role",
    )

    with pytest.raises(StorageUnavailableError):
        snap = s3_signing.signing_snapshot(creds, required_seconds=15 * 60 + 120)
        s3_signing.link_lifetime(snap, max_ttl_seconds=86400, min_ttl_seconds=15 * 60)


@pytest.mark.parametrize("call", ["check", "sign"])
@pytest.mark.asyncio
async def test_credential_discovery_failures_are_storage_unavailable(mode_adapter_factory, monkeypatch, call):
    from botocore.exceptions import CredentialRetrievalError

    from app.adapters.storage.s3 import S3StorageProvider
    from app.exceptions import StorageUnavailableError
    from app.settings_contract import ComputeMode

    adapter = mode_adapter_factory(S3StorageProvider, mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"

    async def none(*a, **k):
        return None

    def broken():
        raise CredentialRetrievalError(provider="iam-role", error_msg="metadata service timed out")

    monkeypatch.setattr(adapter, "_get_credentials", none)
    monkeypatch.setattr(adapter, "_signing_credentials", broken)

    with pytest.raises(StorageUnavailableError) as exc:
        if call == "check":
            await adapter.check_signed_download(min_ttl_seconds=3600)
        else:
            await adapter.sign_download("s3://b/k", max_ttl_seconds=86400, min_ttl_seconds=3600)
    assert "metadata service" not in str(exc.value)


async def _exports(store) -> list:
    return await store.list_objects(await store.resolve_uri(StorageStore.CONFIG_BACKUPS, "exports/"))


def _full_day_signer(store, monkeypatch):
    async def sign(uri, *, max_ttl_seconds, min_ttl_seconds):
        return SignedDownload(url="https://signed.test/x", expires_at=datetime.now(UTC) + timedelta(hours=24))

    monkeypatch.setattr(store, "sign_download", sign)


def _slow_audit(monkeypatch, delay: float) -> None:
    from app.api import data_export

    real_audit = data_export.log_action

    async def slow(**kwargs):
        await asyncio.sleep(delay)
        await real_audit(**kwargs)

    monkeypatch.setattr(data_export, "log_action", slow)


@pytest.mark.parametrize("budget", ["issuance", "handoff"])
@pytest.mark.asyncio
async def test_a_full_day_links_audit_is_bounded_by_the_operation_budgets(
    client, session, admin_user, headers, store, monkeypatch, budget
):
    from app.services import export_delivery

    _full_day_signer(store, monkeypatch)
    if budget == "issuance":
        monkeypatch.setattr(resources, "ISSUE_DEADLINE", 0.5)
    else:
        monkeypatch.setattr(export_delivery, "HANDOFF_SECONDS", 0.2)
    _slow_audit(monkeypatch, 1.0)
    await _seed(session, admin_user.organization_id)

    resp = await _post(client, headers, method="gcs")

    assert resp.status_code != 200
    assert await _exports(store) == []


@pytest.mark.asyncio
async def test_a_direct_exports_audit_commit_is_bounded(client, session, admin_user, headers, store, monkeypatch):
    from app.services import export_delivery

    monkeypatch.setattr(export_delivery, "HANDOFF_SECONDS", 0.2)
    _slow_audit(monkeypatch, 1.0)
    await _seed(session, admin_user.organization_id)

    resp = await _post(client, headers)

    assert resp.status_code == 504
    assert resp.json()["code"] == "export_timed_out"


@pytest.mark.asyncio
async def test_the_preparation_commit_is_inside_the_preparation_deadline(
    session, admin_user, store, tmp_path, monkeypatch
):
    from app.exceptions import ExportTimeoutError
    from app.services import export_service

    await _seed(session, admin_user.organization_id)
    monkeypatch.setattr(resources, "BUILD_DEADLINE", 0.3)
    real_commit = session.commit

    async def slow_commit():
        await asyncio.sleep(1)
        await real_commit()

    monkeypatch.setattr(session, "commit", slow_commit)

    started = time.monotonic()
    with pytest.raises(ExportTimeoutError):
        await export_service.prepare_experiment_export(
            session, 1, admin_user.organization_id, include_fastq=False, include_provenance=False, user_email="a@b"
        )

    assert time.monotonic() - started < 0.9  # refused at the deadline, not after the slow commit


# -- F3: project metadata before admission -------------------------------------------------------


async def _project_with_fastq_links(session, org: int) -> None:
    await session.execute(
        text("INSERT INTO projects (id, organization_id, name, status) VALUES (1, :o, 'P', 'active')"), {"o": org}
    )
    await session.execute(
        text(
            "INSERT INTO experiments (id, uuid, organization_id, project_id, name, status) VALUES "
            "(1, 'aaaaaaaa-0000-0000-0000-000000000001', :o, 1, 'A', 'x'), "
            "(2, 'bbbbbbbb-0000-0000-0000-000000000002', :o, 1, 'B', 'x')"
        ),
        {"o": org},
    )
    await session.execute(
        text(
            "INSERT INTO samples (id, experiment_id, external_id, status) VALUES (11, 2, 'B1', 'r'), (12, 1, 'A1', 'r')"
        )
    )
    for fid, ftype in ((1, "txt"), (2, "fastq.gz"), (3, "fastq.gz")):
        await session.execute(
            text(
                "INSERT INTO files (id, organization_id, filename, gcs_uri, storage_uri, file_type, size_bytes,"
                " source_type) VALUES (:f, :o, :n, 'gs://b/x', 'gs://b/x', :t, 1, 'upload')"
            ),
            {"f": fid, "o": org, "n": f"f{fid}", "t": ftype},
        )
        for sid in (11, 12):
            await session.execute(
                text("INSERT INTO sample_files (sample_id, file_id) VALUES (:s, :f)"), {"s": sid, "f": fid}
            )
    await session.commit()


@pytest.mark.asyncio
async def test_associations_are_read_only_for_the_selected_files(session, admin_user, monkeypatch):
    from app.services.data_export import selection

    seen = []
    real = selection._lowest_linked_experiment

    async def spy(session_, file_ids, project_id, org_id):
        seen.append(sorted(file_ids))
        return await real(session_, file_ids, project_id, org_id)

    monkeypatch.setattr(selection, "_lowest_linked_experiment", spy)
    await _project_with_fastq_links(session, admin_user.organization_id)

    content = await selection.select_project(session, 1, admin_user.organization_id, include_fastq=False, limit=1)

    assert seen == [[1]]
    assert {e.name: [f.id for f in e.files] for e in content.experiments} == {"A": [1], "B": []}


@pytest.mark.asyncio
async def test_a_busy_project_export_selects_nothing(client, session, admin_user, headers, store, monkeypatch):
    from app.services.data_export import selection

    calls = []

    async def spy(*a, **k):
        calls.append(1)

    monkeypatch.setattr(selection, "select_project", spy)
    await _project_with_fastq_links(session, admin_user.organization_id)

    lease = resources.SLOT.try_acquire()
    try:
        resp = await client.post(
            "/api/projects/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=headers,
        )
    finally:
        lease.release()

    assert resp.status_code == 429
    assert calls == []


@pytest.mark.asyncio
async def test_too_many_experiments_are_refused_before_they_are_read(
    client, session, admin_user, headers, store, monkeypatch
):
    monkeypatch.setattr(resources, "MAX_PROJECT_EXPERIMENTS", 1)
    await _project_with_fastq_links(session, admin_user.organization_id)

    resp = await client.post(
        "/api/projects/1/export/data",
        json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
        headers=headers,
    )

    assert resp.status_code == 413
    assert "experiments" in resp.json()["detail"]


# -- F4: the manifest allowance is in bytes ------------------------------------------------------


async def _samples(session, org: int, notes: list[str]) -> None:
    await _seed(session, org)
    for i, note in enumerate(notes):
        await session.execute(
            text("INSERT INTO samples (experiment_id, external_id, status, prep_notes) VALUES (1, :e, 'r', :n)"),
            {"e": f"S{i:05d}", "n": note},
        )
    await session.commit()


@pytest.mark.asyncio
async def test_multibyte_rows_are_refused_at_the_byte_limit_before_more_are_read(session, admin_user, monkeypatch):
    from app.exceptions import ExportLimitError
    from app.services.data_export import selection

    written = []
    real = selection.csv.DictWriter

    class Counting(real):
        def writerow(self, row):
            written.append(1)
            return super().writerow(row)

    monkeypatch.setattr(selection.csv, "DictWriter", Counting)
    await _samples(session, admin_user.organization_id, ["😀" * 300] * 3)  # 1,200 bytes but 300 characters each

    with pytest.raises(ExportLimitError):
        await selection.write_sample_manifest(session, 1, limit_bytes=1_000)

    assert len(written) == 2  # the header (written through writerow) and the first sample only


@pytest.mark.asyncio
async def test_a_streamed_multibyte_manifest_matches_the_reference_across_batches(session, admin_user):
    from sqlalchemy import select

    from app.models.sample import Sample
    from app.services.data_export import selection

    await _samples(session, admin_user.organization_id, [f"é{i}实验😀" for i in range(1100)])
    samples = (await session.execute(select(Sample).where(Sample.experiment_id == 1).order_by(Sample.id))).scalars()

    expected = selection.build_sample_manifest(list(samples)).encode("utf-8")
    streamed = await selection.write_sample_manifest(session, 1, limit_bytes=len(expected))

    assert streamed == expected
