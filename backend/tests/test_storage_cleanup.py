"""A relocated File's superseded source is removed only after its new reference commits (12B.1c).

Relocation records the new URI and one pending cleanup (the old object's pinned identity) in the
caller's transaction. Nothing is deleted before that commits; afterwards the cleanup deletes only the
exact old version, only when nothing else references it, retries a storage failure on a bounded
schedule, and waits while storage writes are paused. Deleting a File accounts for its pending source.
Verification evidence for the old object never describes the new one.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.models import ObjectIdentity
from app.adapters.storage.gcs import GcsStorageProvider
from app.exceptions import StorageUnavailableError
from app.models.file import File
from app.models.file_verification import FileVerification
from app.services import file_verification_service as fvs
from app.services import storage_cleanup


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import gcs

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = GcsStorageProvider()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        yield adapter


def _factory():
    from app import database

    return database.async_session_factory


async def _put(adapter, uri, data=b"science"):
    await adapter.upload_file(uri, io.BytesIO(data))
    return await adapter.object_identity(uri)


@pytest_asyncio.fixture
async def relocated(session, admin_user, store):
    """A committed File moved from its old object to a new one, with the old one pending cleanup."""
    old = await _put(store, "gs://bk/uploads/1/a.txt")
    file = File(
        organization_id=admin_user.organization_id,
        storage_uri=old.uri,
        filename="a.txt",
        file_type="other",
        size_bytes=old.size_bytes,
    )
    session.add(file)
    await session.commit()
    new = await store.copy_create_only(old.uri, f"gs://bk/experiments/1/files/{file.uuid}/a.txt", source_identity=old)
    locked = await storage_cleanup.lock_file_for_storage_change(session, file.id, admin_user.organization_id)
    await storage_cleanup.record_relocation(session, locked, destination=new, previous=old)
    await session.commit()
    return file.id, old, new


async def _file(session, file_id) -> File:
    return await session.get(File, file_id, populate_existing=True)


# ---------------------------------------------------------------------------
# commit boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rolled_back_relocation_keeps_the_original_and_records_nothing(session, admin_user, store):
    old = await _put(store, "gs://bk/uploads/2/a.txt")
    file = File(organization_id=admin_user.organization_id, storage_uri=old.uri, filename="a.txt", file_type="other")
    session.add(file)
    await session.commit()
    file_id = file.id
    new = await store.copy_create_only(old.uri, "gs://bk/experiments/1/files/x/a.txt", source_identity=old)
    locked = await storage_cleanup.lock_file_for_storage_change(session, file_id, admin_user.organization_id)
    await storage_cleanup.record_relocation(session, locked, destination=new, previous=old)
    # Nothing is deleted inside the caller's transaction, even if cleanup is attempted meanwhile.
    assert await storage_cleanup.reconcile_due(_factory()) == 0
    await session.rollback()

    reread = await _file(session, file_id)
    assert reread.storage_uri == old.uri and reread.pending_storage_cleanup is None
    assert await store.read_bytes(old.uri) == b"science"


@pytest.mark.asyncio
async def test_cleanup_after_commit_deletes_only_the_old_source(session, admin_user, store, relocated):
    file_id, old, new = relocated
    reread = await _file(session, file_id)
    assert reread.storage_uri == new.uri
    slot = reread.pending_storage_cleanup
    assert slot is not None
    assert slot["uri"] == old.uri
    assert slot["version"] == old.version

    assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "deleted"

    assert not await store.exists(old.uri)
    assert await store.read_bytes(new.uri) == b"science"
    assert (await _file(session, file_id)).pending_storage_cleanup is None
    # Repeating finds nothing to do.
    assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "none"


@pytest.mark.asyncio
async def test_an_old_source_another_record_still_names_is_kept(session, admin_user, store, relocated):
    file_id, old, _new = relocated
    session.add(File(organization_id=admin_user.organization_id, storage_uri=old.uri, filename="b", file_type="other"))
    await session.commit()

    assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "referenced"
    assert await store.read_bytes(old.uri) == b"science"
    assert (await _file(session, file_id)).pending_storage_cleanup is None


@pytest.mark.asyncio
async def test_a_replaced_old_source_is_kept(session, admin_user, store, relocated):
    file_id, old, _new = relocated
    await store.upload_file(old.uri, io.BytesIO(b"someone else's bytes"))

    assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "changed"
    assert await store.read_bytes(old.uri) == b"someone else's bytes"
    assert (await _file(session, file_id)).pending_storage_cleanup is None


@pytest.mark.asyncio
async def test_a_storage_failure_is_retried_on_a_bounded_schedule(session, admin_user, store, relocated):
    file_id, old, _new = relocated

    async def unavailable(identity):
        raise RuntimeError("503 https://storage.googleapis.com SECRET")

    with patch.object(store, "delete_if_unchanged", unavailable):
        assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "retry"
    slot = (await _file(session, file_id)).pending_storage_cleanup
    assert slot is not None
    assert slot["attempts"] == 1 and slot["next_attempt_at"] is not None
    assert "SECRET" not in str(slot)

    # Not due yet: the periodic pass leaves it.
    assert await storage_cleanup.reconcile_due(_factory()) == 0
    with patch.object(storage_cleanup, "_now", lambda: datetime.now(timezone.utc) + timedelta(hours=1)):
        assert await storage_cleanup.reconcile_due(_factory()) == 1
    assert not await store.exists(old.uri)
    assert (await _file(session, file_id)).pending_storage_cleanup is None


@pytest.mark.asyncio
async def test_automatic_retries_stop_after_the_last_attempt_but_the_record_stays(
    session, admin_user, store, relocated
):
    file_id, old, _new = relocated

    async def unavailable(identity):
        raise RuntimeError("down")

    with patch.object(store, "delete_if_unchanged", unavailable):
        for _ in range(storage_cleanup.MAX_ATTEMPTS):
            assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "retry"
    slot = (await _file(session, file_id)).pending_storage_cleanup
    assert slot is not None
    assert slot["attempts"] == storage_cleanup.MAX_ATTEMPTS and slot["next_attempt_at"] is None
    assert await store.exists(old.uri)


@pytest.mark.asyncio
async def test_cleanup_waits_while_storage_writes_are_paused(session, admin_user, store, relocated):
    from app.services import storage_write_admission

    file_id, old, _new = relocated
    await storage_write_admission.pause_writes(session, None)
    await session.commit()
    try:
        assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "paused"
        assert await store.exists(old.uri)
        assert (await _file(session, file_id)).pending_storage_cleanup is not None
    finally:
        await storage_write_admission.allow_writes(session)
        await session.commit()


@pytest.mark.asyncio
async def test_another_relocation_first_settles_the_pending_one(session, admin_user, store, relocated):
    file_id, old, new = relocated
    newer = await store.copy_create_only(new.uri, "gs://bk/unlinked/files/x/a.txt", source_identity=new)
    locked = await storage_cleanup.lock_file_for_storage_change(session, file_id, admin_user.organization_id)
    await storage_cleanup.record_relocation(session, locked, destination=newer, previous=new)
    await session.commit()
    assert not await store.exists(old.uri)  # settled before the second relocation was recorded
    pending = (await _file(session, file_id)).pending_storage_cleanup
    assert pending is not None and pending["uri"] == new.uri


@pytest.mark.asyncio
async def test_another_organization_cannot_lock_the_file(session, admin_user, store, relocated):
    file_id, _old, _new = relocated
    assert (
        await storage_cleanup.lock_file_for_storage_change(session, file_id, admin_user.organization_id + 999) is None
    )


# ---------------------------------------------------------------------------
# deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_file_also_frees_its_pending_old_source(session, admin_user, store, relocated):
    from app.services.file_service import FileService

    file_id, old, new = relocated
    assert await FileService.delete_file_record(session, file_id, admin_user.organization_id, admin_user.id)
    await session.commit()
    reread = await _file(session, file_id)
    assert reread.storage_deleted and reread.pending_storage_cleanup is None
    assert not await store.exists(old.uri) and not await store.exists(new.uri)


@pytest.mark.asyncio
async def test_a_pending_source_that_cannot_be_freed_stops_the_delete(session, admin_user, store, relocated):
    from app.services.file_service import FileService

    file_id, old, new = relocated

    async def unavailable(identity):
        raise RuntimeError("down")

    with patch.object(store, "delete_if_unchanged", unavailable), pytest.raises(StorageUnavailableError):
        await FileService.delete_file_record(session, file_id, admin_user.organization_id, admin_user.id)
    await session.rollback()
    reread = await _file(session, file_id)
    assert reread.deleted_at is None and reread.pending_storage_cleanup is not None
    assert await store.exists(old.uri) and await store.exists(new.uri)


# ---------------------------------------------------------------------------
# verification follows the bytes
# ---------------------------------------------------------------------------


async def _verified_file(session, admin_user, store, uri, md5="0" * 32):
    old = await _put(store, uri)
    file = File(organization_id=admin_user.organization_id, storage_uri=old.uri, filename="a", file_type="other")
    session.add(file)
    await session.flush()
    file.ready_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session.add(
        FileVerification(
            organization_id=admin_user.organization_id,
            file_id=file.id,
            status=fvs.VERIFIED,
            expected_algorithm="md5",
            expected_checksum=md5,
            expectation_resolved=True,
            expectation_source="upload_assertion",
            observed_checksum=md5,
            observed_method="streamed",
            observed_uri=old.uri,
            observed_object_version=old.version,
            verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    await session.commit()
    return file, old


async def _relocate(session, file, destination, previous, org_id):
    file_id = file.id
    locked = await storage_cleanup.lock_file_for_storage_change(session, file_id, org_id)
    await storage_cleanup.record_relocation(session, locked, destination=destination, previous=previous)
    await session.commit()
    query = select(FileVerification).where(FileVerification.file_id == file_id)
    return (await session.execute(query.execution_options(populate_existing=True))).scalar_one()


@pytest.mark.asyncio
async def test_a_verified_file_is_reverified_at_its_new_object_from_a_native_digest(session, admin_user, store):
    file, old = await _verified_file(session, admin_user, store, "gs://bk/uploads/3/a.txt")
    dest = ObjectIdentity(uri="gs://bk/experiments/1/files/x/a.txt", size_bytes=7, version="v2", native_md5="0" * 32)
    record = await _relocate(session, file, dest, old, admin_user.organization_id)
    assert record.status == fvs.VERIFIED
    assert (record.observed_uri, record.observed_object_version, record.observed_method) == (dest.uri, "v2", "provider")
    assert record.expectation_revision == 2
    assert (await _file(session, file.id)).ready_at == datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_without_a_native_digest_the_new_object_is_queued_not_assumed(session, admin_user, store):
    file, old = await _verified_file(session, admin_user, store, "gs://bk/uploads/4/a.txt")
    dest = ObjectIdentity(uri="gs://bk/experiments/1/files/y/a.txt", size_bytes=7, version="v2")
    record = await _relocate(session, file, dest, old, admin_user.organization_id)
    assert record.status == fvs.QUEUED
    assert record.observed_uri is None and record.observed_checksum is None
    assert record.expected_checksum == "0" * 32  # the same expectation, re-established
    assert (await _file(session, file.id)).ready_at is not None  # readiness history is not reset


@pytest.mark.asyncio
async def test_a_mismatch_is_never_promoted_by_a_copy(session, admin_user, store):
    file, old = await _verified_file(session, admin_user, store, "gs://bk/uploads/5/a.txt")
    rec = (await session.execute(select(FileVerification))).scalar_one()
    rec.status, rec.observed_checksum, rec.error_code = fvs.CHECKSUM_MISMATCH, "f" * 32, "checksum_mismatch"
    await session.commit()
    dest = ObjectIdentity(uri="gs://bk/experiments/1/files/z/a.txt", size_bytes=7, version="v2", native_md5="f" * 32)
    record = await _relocate(session, file, dest, old, admin_user.organization_id)
    assert record.status == fvs.CHECKSUM_MISMATCH


@pytest.mark.asyncio
async def test_an_in_flight_verification_of_the_old_object_cannot_publish(session, admin_user, store):
    from app.services import file_verification_worker as worker

    file, old = await _verified_file(session, admin_user, store, "gs://bk/uploads/6/a.txt")
    rec = (await session.execute(select(FileVerification))).scalar_one()
    rec.status, rec.next_attempt_at = fvs.QUEUED, None
    await session.commit()
    claim = await worker.claim_next()
    assert claim is not None and claim.uri == old.uri
    dest = ObjectIdentity(uri="gs://bk/experiments/1/files/w/a.txt", size_bytes=7, version="v2")
    await _relocate(session, file, dest, old, admin_user.organization_id)
    from app.adapters.models import ObjectDigest

    digest = ObjectDigest(uri=old.uri, hex="0" * 32, size_bytes=7, version=old.version, method="streamed")
    assert await worker.complete(claim, worker.Result(digest=digest, final_uri=old.uri)) in ("stale", "lost")
    query = select(FileVerification).execution_options(populate_existing=True)
    record = (await session.execute(query)).scalar_one()
    assert record.status == fvs.QUEUED and record.observed_uri is None


# ---------------------------------------------------------------------------
# ownership: the periodic pass, and the migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_periodic_job_runs_the_cleanup_pass(session, admin_user, store, relocated):
    from app.tasks.jobs import PERIODIC_JOBS

    file_id, old, _new = relocated
    [job] = [j for j in PERIODIC_JOBS if j.role == "storage-cleanup"]
    with patch("app.tasks.jobs._factory", _factory):
        await job.prepare()()
    assert not await store.exists(old.uri)
    assert (await _file(session, file_id)).pending_storage_cleanup is None


@pytest.mark.asyncio
async def test_migration_146_adds_a_nullable_slot(session, admin_user):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "146_file_pending_storage_cleanup.py"

    def run(sync_conn, step):
        spec = importlib.util.spec_from_file_location("migration_146", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(sync_conn)):
            getattr(module, step)()

    org = admin_user.organization_id
    await session.commit()
    conn = await session.connection()
    await conn.run_sync(run, "downgrade")
    await session.execute(
        text(
            "INSERT INTO files (organization_id, gcs_uri, storage_uri, filename, file_type) "
            "VALUES (:org, 'gs://b/x', 'gs://b/x', 'x', 'other')"
        ).bindparams(org=org)
    )
    conn = await session.connection()
    await conn.run_sync(run, "upgrade")
    assert (await session.execute(text("SELECT pending_storage_cleanup FROM files"))).scalar_one() is None
    await session.rollback()


# ---------------------------------------------------------------------------
# selection (review finding 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_settled_cleanup_is_sql_null(session, admin_user, store, relocated):
    from sqlalchemy import text

    file_id, _old, _new = relocated
    assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "deleted"
    is_null = (
        await session.execute(text("SELECT pending_storage_cleanup IS NULL FROM files WHERE id = :i"), {"i": file_id})
    ).scalar_one()
    assert is_null is True


@pytest.mark.asyncio
async def test_settled_and_waiting_records_never_starve_due_work(session, admin_user, store):
    from sqlalchemy import text

    org = admin_user.organization_id
    # Many earlier rows: settled (JSON null written by older code), out of attempts, or not due yet.
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    for n in range(12):
        session.add(File(organization_id=org, storage_uri=f"gs://bk/x/{n}", filename="x", file_type="other"))
    await session.flush()
    await session.execute(text("UPDATE files SET pending_storage_cleanup = 'null'::jsonb WHERE id % 3 = 0"))
    await session.execute(
        text(
            "UPDATE files SET pending_storage_cleanup = jsonb_build_object('uri','gs://bk/gone','version','v',"
            "'size_bytes',1,'attempts',5,'next_attempt_at',NULL) WHERE id % 3 = 1"
        )
    )
    await session.execute(
        text(
            "UPDATE files SET pending_storage_cleanup = jsonb_build_object('uri','gs://bk/gone','version','v',"
            "'size_bytes',1,'attempts',1,'next_attempt_at',:later) WHERE id % 3 = 2"
        ).bindparams(later=later)
    )
    await session.commit()
    due_old = await _put(store, "gs://bk/uploads/due/a.txt")
    due = File(organization_id=org, storage_uri="gs://bk/experiments/1/files/d/a.txt", filename="a", file_type="other")
    due.pending_storage_cleanup = storage_cleanup.cleanup_slot(due_old)
    session.add(due)
    await session.commit()

    assert await storage_cleanup.reconcile_due(_factory(), limit=2) == 1
    assert not await store.exists(due_old.uri)
