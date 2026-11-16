"""01B.4: the durable checksum verification worker.

Work is claimed with a committed lease, copied/hashed with no database transaction open, and applied
in a short transaction that rechecks the lease, expectation revision, file identity and deletion.
Only a match on the current object can make a file ready, and only through FileService.mark_ready.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update

from app import database as database_module
from app.adapters.models import ObjectDigest, ObjectIdentity, StorageObjectNotFound
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.pipeline_run import PipelineRun
from app.services import file_verification_worker as worker
from app.services.event_types import FILE_VERIFICATION_FAILED, INTEGRATION_FILE_READY, INTEGRATION_FILE_REGISTERED
from app.services.file_service import FileService
from app.services.file_verification_service import Expectation, FileVerificationService
from tests.support.event_capture import EventCapture

pytestmark = pytest.mark.asyncio

DATA = b"@r\nACGT\n+\nIIII\n" * 5000
GOOD = hashlib.md5(DATA).hexdigest()
BAD = "f" * 32


@pytest_asyncio.fixture
async def events():
    capture = EventCapture([INTEGRATION_FILE_READY, INTEGRATION_FILE_REGISTERED, FILE_VERIFICATION_FAILED])
    yield capture
    capture.close()


@pytest.fixture
def s3_local(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import s3 as module

    monkeypatch.setattr(module, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = S3StorageProvider(org_slug="t")
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
    return adapter


async def _file(session, org_id, uri, **kw) -> File:
    f = await FileService.create_file_record(
        session,
        org_id=org_id,
        user_id=None,
        filename=uri.rsplit("/", 1)[-1],
        storage_uri=uri,
        size_bytes=None,
        md5_checksum=None,
        file_type="fastq",
        **kw,
    )
    return f


async def _expect(session, admin_user, adapter, md5=GOOD, uri="s3://bioaf-raw-t/up/a.fastq.gz", data=DATA, **kw):
    await adapter.write_bytes(uri, data)
    f = await _file(session, admin_user.organization_id, uri, **kw)
    await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=md5)
    await session.commit()
    return f.id


async def _record(file_id) -> FileVerification:
    async with database_module.async_session_factory() as s:
        return (await s.execute(select(FileVerification).where(FileVerification.file_id == file_id))).scalar_one()


async def _file_row(file_id) -> File:
    async with database_module.async_session_factory() as s:
        return await s.get(File, file_id)


async def _make_due(file_id):
    async with database_module.async_session_factory() as s:
        await s.execute(
            update(FileVerification)
            .where(FileVerification.file_id == file_id)
            .values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await s.commit()


# --- outcomes ---------------------------------------------------------------------------------


async def test_a_streamed_match_verifies_and_makes_the_file_ready_once(session, admin_user, s3_local, events):
    file_id = await _expect(session, admin_user, s3_local)

    assert await worker.run_once() is True
    assert await worker.run_once() is False  # nothing left
    await events.settle()

    record = await _record(file_id)
    assert record.status == "verified" and record.observed_checksum == GOOD and record.observed_method == "streamed"
    assert record.observed_bytes == len(DATA) and record.verified_at is not None
    assert record.lease_token is None and record.bytes_processed == len(DATA)
    f = await _file_row(file_id)
    assert f.ready_at is not None and f.md5_checksum == GOOD and f.size_bytes == len(DATA)
    assert len(events.of(INTEGRATION_FILE_READY)) == 1
    assert events.of(INTEGRATION_FILE_REGISTERED) == []  # the worker never announces registration


async def test_a_mismatch_keeps_both_values_and_the_file_unready(session, admin_user, s3_local, events):
    file_id = await _expect(session, admin_user, s3_local, md5=BAD)

    await worker.run_once()
    await events.settle()

    record = await _record(file_id)
    assert record.status == "checksum_mismatch"
    assert record.expected_checksum == BAD and record.observed_checksum == GOOD
    assert record.retryable is False and record.next_attempt_at is None
    assert (await _file_row(file_id)).ready_at is None
    assert events.of(INTEGRATION_FILE_READY) == []
    [notice] = events.of(FILE_VERIFICATION_FAILED)
    assert notice["entity_type"] == "file" and notice["entity_id"] == file_id
    assert notice["metadata"]["error_code"] == "checksum_mismatch"
    assert BAD not in notice["message"] and "s3://" not in notice["message"]
    assert await worker.run_once() is False  # identical immutable inputs are not retried


class _Fake:
    """A storage adapter double recording what the worker asked of it."""

    def __init__(self, *, native=None, error=None, size=len(DATA)):
        self.native = native
        self.error = error
        self.size = size
        self.streamed = 0
        self.on_stream = None

    async def object_identity(self, uri):
        if self.error is not None:
            raise self.error
        return ObjectIdentity(uri=uri, size_bytes=self.size, version="v1", native_md5=self.native)

    async def stream_md5(self, identity, *, chunk_size=None, on_progress=None):
        self.streamed += 1
        if self.on_stream is not None:
            await self.on_stream(on_progress)
        if on_progress is not None:
            await on_progress(identity.size_bytes)
        return ObjectDigest(uri=identity.uri, hex=GOOD, size_bytes=identity.size_bytes, version="v1", method="streamed")


@pytest.fixture
def fake(monkeypatch):
    adapter = _Fake()
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
    return adapter


async def _expect_fake(session, admin_user, md5=GOOD, uri="s3://b/up/a.fastq.gz", **kw):
    f = await _file(session, admin_user.organization_id, uri, **kw)
    await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=md5)
    await session.commit()
    return f.id


async def test_a_trusted_native_digest_needs_no_read(session, admin_user, fake, events):
    fake.native = GOOD
    file_id = await _expect_fake(session, admin_user)
    await worker.run_once()
    await events.settle()
    record = await _record(file_id)
    assert record.status == "verified" and record.observed_method == "provider"
    assert fake.streamed == 0
    assert len(events.of(INTEGRATION_FILE_READY)) == 1


async def test_transient_failures_follow_the_schedule_then_fail_visibly(session, admin_user, fake, events):
    fake.error = ConnectionError("network down")
    file_id = await _expect_fake(session, admin_user)

    delays = []
    for attempt in range(1, 6):
        before = datetime.now(timezone.utc)
        assert await worker.run_once() is True
        record = await _record(file_id)
        assert record.attempt_count == attempt
        if attempt < 5:
            assert record.status == "queued" and record.error_code == "storage_unavailable" and record.retryable
            delays.append(round((record.next_attempt_at - before).total_seconds() / 10) * 10)
            await _make_due(file_id)
    assert delays == [60, 300, 1800, 7200]
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == "storage_unavailable" and record.next_attempt_at is None
    await events.settle()
    assert [n["metadata"]["error_code"] for n in events.of(FILE_VERIFICATION_FAILED)] == ["storage_unavailable"]
    assert await worker.run_once() is False


@pytest.mark.parametrize(
    ("error", "code"),
    [(PermissionError("denied"), "storage_permission_denied")],
)
async def test_permission_failures_need_correction(session, admin_user, fake, error, code):
    fake.error = error
    file_id = await _expect_fake(session, admin_user)
    await worker.run_once()
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == code and record.retryable is False
    assert record.attempt_count == 1


async def test_a_missing_object_is_retried(session, admin_user, fake):
    fake.error = StorageObjectNotFound("s3://b/up/a.fastq.gz")
    file_id = await _expect_fake(session, admin_user)
    await worker.run_once()
    record = await _record(file_id)
    assert record.status == "queued" and record.error_code == "object_missing"


async def test_an_authorized_retry_requeues_without_another_registration(session, admin_user, fake, events):
    fake.error = PermissionError("denied")
    file_id = await _expect_fake(session, admin_user)
    await worker.run_once()
    fake.error = None  # the administrator corrected the permissions

    async with database_module.async_session_factory() as s:
        record = await FileVerificationService.retry(s, file_id, admin_user.organization_id)
        assert record.status == "queued" and record.attempt_count == 0
        await s.commit()
    await worker.run_once()
    await events.settle()
    assert (await _record(file_id)).status == "verified"
    assert events.of(INTEGRATION_FILE_REGISTERED) == []
    assert len(events.of(INTEGRATION_FILE_READY)) == 1


async def test_retry_refuses_work_in_progress_and_other_organizations(session, admin_user, fake):
    from app.exceptions import StateError

    file_id = await _expect_fake(session, admin_user)
    async with database_module.async_session_factory() as s:
        with pytest.raises(StateError):
            await FileVerificationService.retry(s, file_id, admin_user.organization_id)
        assert await FileVerificationService.retry(s, file_id, admin_user.organization_id + 999) is None


# --- leases, staleness, and transactions ------------------------------------------------------


async def test_an_active_lease_is_not_claimed_and_an_expired_one_is(session, admin_user, fake):
    file_id = await _expect_fake(session, admin_user)
    async with database_module.async_session_factory() as s:
        await s.execute(
            update(FileVerification)
            .where(FileVerification.file_id == file_id)
            .values(
                status="verifying",
                lease_token=uuid.uuid4(),
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                attempt_count=1,
            )
        )
        await s.commit()
    assert await worker.run_once() is False

    async with database_module.async_session_factory() as s:
        await s.execute(
            update(FileVerification)
            .where(FileVerification.file_id == file_id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await s.commit()
    assert await worker.run_once() is True
    record = await _record(file_id)
    assert record.status == "verified" and record.attempt_count == 2  # recovery keeps the budget


async def test_a_changed_expectation_discards_the_in_flight_result(session, admin_user, fake):
    file_id = await _expect_fake(session, admin_user)

    async def change_expectation(_progress):
        async with database_module.async_session_factory() as s:
            f = await s.get(File, file_id)
            await FileVerificationService.record_expectation(s, f, source="upload_assertion", md5=BAD)
            await s.commit()

    fake.on_stream = change_expectation
    await worker.run_once()
    record = await _record(file_id)
    assert record.expectation_revision == 2 and record.expected_checksum == BAD
    assert record.status == "queued" and record.observed_checksum is None
    assert (await _file_row(file_id)).ready_at is None


async def test_a_lost_lease_stops_the_attempt_without_writing(session, admin_user, fake):
    file_id = await _expect_fake(session, admin_user)
    stolen = uuid.uuid4()

    async def steal(on_progress):
        async with database_module.async_session_factory() as s:
            await s.execute(
                update(FileVerification).where(FileVerification.file_id == file_id).values(lease_token=stolen)
            )
            await s.commit()

    fake.on_stream = steal
    await worker.run_once()
    record = await _record(file_id)
    assert record.lease_token == stolen and record.status == "verifying" and record.observed_checksum is None


async def test_no_row_lock_is_held_during_storage_io(session, admin_user, fake):
    file_id = await _expect_fake(session, admin_user)
    observed = {}

    async def probe(_progress):
        async with database_module.async_session_factory() as s:
            await s.execute(text("SET LOCAL lock_timeout = '200ms'"))
            await s.execute(
                select(FileVerification).where(FileVerification.file_id == file_id).with_for_update(nowait=True)
            )
            await s.execute(select(File).where(File.id == file_id).with_for_update(nowait=True))
            observed["locked"] = False
            await s.rollback()

    fake.on_stream = probe
    await worker.run_once()
    assert observed == {"locked": False}


async def test_a_file_deleted_during_verification_is_not_made_ready(session, admin_user, fake, events):
    file_id = await _expect_fake(session, admin_user)

    async def delete(_progress):
        async with database_module.async_session_factory() as s:
            await s.execute(update(File).where(File.id == file_id).values(deleted_at=datetime.now(timezone.utc)))
            await s.commit()

    fake.on_stream = delete
    await worker.run_once()
    await events.settle()
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == "file_deleted"
    assert (await _file_row(file_id)).ready_at is None
    assert events.of(INTEGRATION_FILE_READY) == []


async def test_cancellation_leaves_recoverable_work(session, admin_user, fake):
    file_id = await _expect_fake(session, admin_user)
    started = asyncio.Event()

    async def hang(_progress):
        started.set()
        await asyncio.sleep(30)

    fake.on_stream = hang
    task = asyncio.create_task(worker.run_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    record = await _record(file_id)
    assert record.status == "verifying" and record.lease_token is not None
    assert (await _file_row(file_id)).ready_at is None


async def test_a_verified_proof_is_reused_not_recomputed(session, admin_user, fake):
    file_id = await _expect_fake(session, admin_user)
    await worker.run_once()
    async with database_module.async_session_factory() as s:
        f = await s.get(File, file_id)
        await FileVerificationService.record_expectation(s, f, source="upload_assertion", md5=GOOD)
        await s.commit()
    assert await worker.run_once() is False
    assert fake.streamed == 1


# --- expectation discovery --------------------------------------------------------------------


async def test_discovery_resolves_to_a_required_check_or_not_required(session, admin_user, fake, monkeypatch, events):
    answers = {"a": GOOD, "b": None}

    async def resolver(s, file, context):
        return answers[context["key"]]

    monkeypatch.setitem(worker.DISCOVERY_RESOLVERS, "fetchngs_csv", resolver)
    ids = {}
    for key in ("a", "b"):
        f = await _file(session, admin_user.organization_id, f"s3://b/{key}.fastq.gz")
        await FileVerificationService.await_expectation(session, f, source="fetchngs_csv", context={"key": key})
        ids[key] = f.id
    await session.commit()

    while await worker.run_once():
        pass
    await events.settle()

    assert (await _record(ids["a"])).status == "verified"
    assert (await _record(ids["b"])).status == "not_required"
    assert (await _file_row(ids["a"])).ready_at is not None
    assert (await _file_row(ids["b"])).ready_at is not None
    assert len(events.of(INTEGRATION_FILE_READY)) == 2


async def test_discovery_failures_retry_then_fail_visibly(session, admin_user, fake, monkeypatch, events):
    async def resolver(s, file, context):
        raise OSError("samplesheet unreadable")

    monkeypatch.setitem(worker.DISCOVERY_RESOLVERS, "fetchngs_csv", resolver)
    f = await _file(session, admin_user.organization_id, "s3://b/x.fastq.gz")
    await FileVerificationService.await_expectation(session, f, source="fetchngs_csv", context={})
    await session.commit()
    file_id = f.id
    for attempt in range(1, 6):
        await worker.run_once()
        record = await _record(file_id)
        if attempt < 5:
            assert record.status == "waiting_expectation" and record.error_code == "expectation_discovery_failed"
            await _make_due(file_id)
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == "expectation_discovery_failed"
    assert (await _file_row(file_id)).ready_at is None
    await events.settle()
    assert len(events.of(FILE_VERIFICATION_FAILED)) == 1


# --- NFS protected copies ---------------------------------------------------------------------


@pytest.fixture
def nfs(monkeypatch, tmp_path):
    from app.config import settings

    root = tmp_path / "nfs"
    (root / "raw" / "verified-files").mkdir(parents=True)
    for path in (root, root / "raw", root / "raw" / "verified-files"):
        os.chmod(path, 0o755)
    adapter = NfsStorageProvider(root=str(root))
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
    monkeypatch.setattr(settings, "nfs_producer_uids", str(os.geteuid() + 1))
    monkeypatch.setattr(settings, "nfs_managed_copy_reserve_bytes", 0)
    return adapter, root


async def _pipeline_output(session, admin_user, adapter, uri, md5):
    run = PipelineRun(organization_id=admin_user.organization_id, pipeline_name="fetchngs", status="completed")
    session.add(run)
    await session.flush()
    await adapter.write_bytes(uri, DATA)
    f = await _file(
        session, admin_user.organization_id, uri, source_type="pipeline_output", source_pipeline_run_id=run.id
    )
    await FileVerificationService.record_expectation(session, f, source="fetchngs_csv", md5=md5)
    await session.commit()
    return f.id


async def test_nfs_verifies_a_protected_copy_and_installs_it(session, admin_user, nfs, events):
    adapter, root = nfs
    source = "file://raw/runs/1/fastq/a.fastq.gz"
    file_id = await _pipeline_output(session, admin_user, adapter, source, GOOD)

    await worker.run_once()
    await events.settle()

    record = await _record(file_id)
    f = await _file_row(file_id)
    assert record.status == "verified" and record.observed_method == "streamed"
    assert f.storage_uri.startswith("file://raw/verified-files/") and f.storage_uri == record.observed_uri
    assert f.gcs_uri == f.storage_uri  # the legacy mirror follows
    assert f.source_storage_uri == source  # durable source identity survives relocation
    assert f.ready_at is not None
    assert await adapter.exists(source)  # the producer's copy is retained
    artifact = adapter._fs.path(f.storage_uri)
    assert stat.S_IMODE(os.stat(artifact).st_mode) == 0o444
    assert len(events.of(INTEGRATION_FILE_READY)) == 1

    # Rediscovering the retained original finds the same File.
    found = await FileService.find_registered_outputs(
        session, admin_user.organization_id, f.source_pipeline_run_id, [source]
    )
    assert found[source].id == file_id


async def test_nfs_mismatch_on_the_copy_keeps_the_source_path_unready(session, admin_user, nfs):
    adapter, _ = nfs
    source = "file://raw/runs/1/fastq/b.fastq.gz"
    file_id = await _pipeline_output(session, admin_user, adapter, source, BAD)
    await worker.run_once()
    record = await _record(file_id)
    f = await _file_row(file_id)
    assert record.status == "checksum_mismatch" and f.storage_uri == source and f.ready_at is None
    assert record.artifact_uri is not None  # kept for bounded cleanup on retry


async def test_nfs_without_separation_refuses_checksum_finalization(session, admin_user, nfs, monkeypatch):
    from app.config import settings

    adapter, root = nfs
    monkeypatch.setattr(settings, "nfs_producer_uids", "")
    file_id = await _pipeline_output(session, admin_user, adapter, "file://raw/runs/1/fastq/c.fastq.gz", GOOD)
    await worker.run_once()
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == "nfs_separation_unavailable"
    assert record.retryable is False
    assert not any((root / "raw" / "verified-files").iterdir())


async def test_nfs_recovery_discards_the_abandoned_artifact(session, admin_user, nfs):
    adapter, root = nfs
    source = "file://raw/runs/1/fastq/d.fastq.gz"
    file_id = await _pipeline_output(session, admin_user, adapter, source, GOOD)
    f = await _file_row(file_id)
    abandoned = uuid.uuid4()
    orphan = await adapter.prepare_managed_copy(
        source, dest_store="raw", file_uuid=f.uuid, artifact_uuid=abandoned, reserve_bytes=0
    )
    async with database_module.async_session_factory() as s:
        await s.execute(
            update(FileVerification)
            .where(FileVerification.file_id == file_id)
            .values(
                status="preparing",
                artifact_uuid=abandoned,
                artifact_uri=orphan.uri,
                lease_token=uuid.uuid4(),
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                attempt_count=1,
            )
        )
        await s.commit()

    await worker.run_once()

    record = await _record(file_id)
    assert record.status == "verified" and record.artifact_uuid != abandoned
    assert not await adapter.exists(orphan.uri)
    assert (await _file_row(file_id)).storage_uri == record.artifact_uri


async def test_reverifying_never_deletes_the_files_current_canonical_artifact(session, admin_user, nfs):
    adapter, _ = nfs
    source = "file://raw/runs/1/fastq/e.fastq.gz"
    file_id = await _pipeline_output(session, admin_user, adapter, source, GOOD)
    await worker.run_once()
    canonical = (await _file_row(file_id)).storage_uri

    async with database_module.async_session_factory() as s:
        f = await s.get(File, file_id)
        await FileVerificationService.record_expectation(s, f, source="fetchngs_csv", md5=BAD)
        await s.commit()
    await worker.run_once()

    assert await adapter.exists(canonical)
    assert (await _file_row(file_id)).storage_uri == canonical
    assert (await _record(file_id)).status == "checksum_mismatch"


async def test_a_stale_nfs_result_leaves_no_artifact_behind(session, admin_user, nfs, monkeypatch):
    adapter, root = nfs
    source = "file://raw/runs/1/fastq/f.fastq.gz"
    file_id = await _pipeline_output(session, admin_user, adapter, source, GOOD)
    real_stream = adapter.stream_md5

    async def stream_then_change(identity, **kw):
        digest = await real_stream(identity, **kw)
        async with database_module.async_session_factory() as s:
            f = await s.get(File, file_id)
            await FileVerificationService.record_expectation(s, f, source="fetchngs_csv", md5=BAD)
            await s.commit()
        return digest

    monkeypatch.setattr(adapter, "stream_md5", stream_then_change)
    await worker.run_once()
    assert not list((root / "raw" / "verified-files").rglob("payload"))
    assert (await _file_row(file_id)).storage_uri == source


# --- recovery of checksum-optional files and terminal discovery failures ----------------------


async def test_a_checksum_optional_file_recovers_through_retry(session, admin_user, fake, events):
    """No checksum was ever required: a retry repeats the readiness read, never checksum discovery."""
    fake.error = ConnectionError("storage down")
    f = await _file(session, admin_user.organization_id, "s3://b/up/plain.csv")
    await FileVerificationService.begin(session, f, Expectation.none("upload"), expected_size=None)
    await session.commit()
    file_id = f.id
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == "storage_unavailable"

    fake.error = None  # storage restored
    async with database_module.async_session_factory() as s:
        record = await FileVerificationService.retry(s, file_id, admin_user.organization_id)
        await s.commit()
    await worker.run_once()
    await events.settle()
    record = await _record(file_id)
    assert record.status == "not_required" and record.error_code is None
    assert (await _file_row(file_id)).ready_at is not None
    assert len(events.of(INTEGRATION_FILE_READY)) == 1


async def test_reattaching_never_resurrects_a_terminal_discovery_failure(session, admin_user, fake, monkeypatch):
    async def resolver(s, file, context):
        return "not-an-md5"  # malformed: discovery fails terminally

    monkeypatch.setitem(worker.DISCOVERY_RESOLVERS, "fetchngs_csv", resolver)
    f = await _file(session, admin_user.organization_id, "s3://b/y.fastq.gz")
    await FileVerificationService.await_expectation(session, f, source="fetchngs_csv", context={})
    await session.commit()
    file_id = f.id
    await worker.run_once()
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == "expectation_discovery_failed"

    # The validation driver attaches the same fetched file again on its next tick.
    async with database_module.async_session_factory() as s:
        again = await s.get(File, file_id)
        await FileVerificationService.await_expectation(s, again, source="fetchngs_csv", context={})
        await s.commit()
    record = await _record(file_id)
    assert record.status == "failed" and record.error_code == "expectation_discovery_failed"
    assert record.next_attempt_at is None
    assert await worker.run_once() is False


async def test_reattaching_keeps_a_scheduled_discovery_backoff(session, admin_user, fake, monkeypatch):
    async def resolver(s, file, context):
        raise OSError("samplesheet unreadable")

    monkeypatch.setitem(worker.DISCOVERY_RESOLVERS, "fetchngs_csv", resolver)
    f = await _file(session, admin_user.organization_id, "s3://b/z.fastq.gz")
    await FileVerificationService.await_expectation(session, f, source="fetchngs_csv", context={})
    await session.commit()
    file_id = f.id
    await worker.run_once()
    scheduled = (await _record(file_id)).next_attempt_at
    async with database_module.async_session_factory() as s:
        again = await s.get(File, file_id)
        await FileVerificationService.await_expectation(s, again, source="fetchngs_csv", context={})
        await s.commit()
    record = await _record(file_id)
    assert record.next_attempt_at == scheduled and record.attempt_count == 1


# --- bounded concurrency ----------------------------------------------------------------------


def test_one_active_digest_job_is_the_default_and_concurrency_is_bounded():
    from pydantic import ValidationError

    from app.config import Settings

    assert Settings().file_verification_concurrency == 1
    assert Settings(file_verification_concurrency=4).file_verification_concurrency == 4
    for bad in (0, -1, 9):
        with pytest.raises(ValidationError):
            Settings(file_verification_concurrency=bad)


async def test_configured_concurrency_runs_that_many_jobs_at_once(session, admin_user, fake, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "file_verification_concurrency", 2)
    ids = [await _expect_fake(session, admin_user, uri=f"s3://b/up/{name}.fastq.gz") for name in ("one", "two")]
    active, peak, both = 0, 0, asyncio.Event()

    async def overlap(_progress):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both.set()
        try:
            await asyncio.wait_for(both.wait(), timeout=5)
        finally:
            active -= 1

    fake.on_stream = overlap
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run_worker_loop(stop))
    await asyncio.wait_for(both.wait(), timeout=10)
    for _ in range(100):
        if [(await _record(i)).status for i in ids] == ["verified", "verified"]:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(loop, timeout=10)
    assert peak == 2
    assert [(await _record(i)).status for i in ids] == ["verified", "verified"]
