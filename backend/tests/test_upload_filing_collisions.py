"""Same-name uploads stay independent, and filing never claims or deletes the wrong object (12B.1d).

An upload into an experiment is copied create-only to ``experiments/{id}/files/{file_uuid}/{name}``
with server-owned tags, registered there, and its ingest copy is removed only after the File
commits. A retry reuses the reserved destination and adopts only an object that is provably its own.
Cancellation is committed before anything is deleted and removes only owned copies.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.file import File
from app.models.upload_checkpoint import UploadCheckpoint
from app.services.upload_service import UploadService
from tests.test_upload_recovery import _adapter, _auth, _checkpoints, _experiment


@pytest_asyncio.fixture
async def raw_storage(session, admin_user):
    from app.models.component import PlatformConfig

    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test"))
    session.add(PlatformConfig(key="raw_bucket_name", value="bioaf-raw-test"))
    await session.commit()


def _quiet():
    return (
        patch("app.services.upload_service.enrich_from_header", AsyncMock()),
        patch("app.services.file_verification_service.FileVerificationService.begin", AsyncMock()),
    )


async def _upload(client, token, adapter, experiment_id, name, data):
    enrich, begin = _quiet()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
        return await client.post(
            f"/api/files/upload/simple?experiment_id={experiment_id}",
            files={"file": (name, io.BytesIO(data), "text/plain")},
            headers=_auth(token),
        )


def _owner(cp: UploadCheckpoint) -> dict:
    return {
        "bioaf-file-uuid": str(cp.file_uuid),
        "bioaf-organization-id": str(cp.organization_id),
        "bioaf-upload-id": cp.id,
    }


# ---------------------------------------------------------------------------
# same-name uploads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_name_uploads_to_one_experiment_preserve_both_files(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter()

    first = await _upload(client, admin_token, adapter, exp.id, "same.txt", b"first-science")
    second = await _upload(client, admin_token, adapter, exp.id, "same.txt", b"second-science")

    assert first.status_code == second.status_code == 200, (first.text, second.text)
    a, b = first.json(), second.json()
    assert a["id"] != b["id"] and a["storage_uri"] != b["storage_uri"]
    assert adapter.objects[a["storage_uri"]].data == b"first-science"
    assert adapter.objects[b["storage_uri"]].data == b"second-science"
    files = {f.id: f for f in (await session.execute(select(File))).scalars()}
    for body in (a, b):
        file = files[body["id"]]
        assert body["storage_uri"] == f"gs://bioaf-raw-test/experiments/{exp.id}/files/{file.uuid}/same.txt"
        assert file.filename == "same.txt" and file.pending_storage_cleanup is None
    # The ingest copies were removed after each File committed.
    assert [uri for uri in adapter.objects if "/uploads/" in uri] == []


@pytest.mark.asyncio
async def test_overlapping_same_name_uploads_preserve_both_files(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter()
    enrich, begin = _quiet()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
        responses = await asyncio.gather(
            *[
                client.post(
                    f"/api/files/upload/simple?experiment_id={exp.id}",
                    files={"file": ("same.txt", io.BytesIO(f"science-{n}".encode()), "text/plain")},
                    headers=_auth(admin_token),
                )
                for n in range(3)
            ]
        )
    assert [r.status_code for r in responses] == [200, 200, 200]
    stored = sorted(adapter.objects[r.json()["storage_uri"]].data for r in responses)
    assert stored == [b"science-0", b"science-1", b"science-2"]


@pytest.mark.asyncio
async def test_same_name_direct_uploads_preserve_both_files(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter()
    enrich, begin = _quiet()
    results = []
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
        for data in (b"lane-one", b"lane-two"):
            init = await client.post(
                "/api/files/upload/initiate",
                json={"filename": "reads.fastq.gz", "experiment_id": exp.id},
                headers=_auth(admin_token),
            )
            assert init.status_code == 200, init.text
            adapter.put(init.json()["gcs_uri"], data)  # the browser sends the bytes to the session
            done = await client.post(
                "/api/files/upload/complete",
                json={"upload_id": init.json()["upload_id"], "actual_md5": ""},
                headers=_auth(admin_token),
            )
            assert done.status_code == 200, done.text
            results.append(done.json())
    assert results[0]["storage_uri"] != results[1]["storage_uri"]
    assert [adapter.objects[r["storage_uri"]].data for r in results] == [b"lane-one", b"lane-two"]


# ---------------------------------------------------------------------------
# interruption and retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lost_copy_response_is_recovered_without_a_second_copy_or_file(
    client, admin_token, raw_storage, session
):
    exp = await _experiment(session)
    adapter = _adapter()

    def lost(source, dest):
        adapter.after.pop("copy_create_only")
        raise RuntimeError("connection reset after the copy was published")

    adapter.after["copy_create_only"] = lost
    with patch("app.services.upload_service.defer_event") as deferred:
        first = await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
        assert first.status_code == 202
        [cp] = await _checkpoints(session)
        assert cp.stage == "received" and cp.destination_version is None
        published = adapter.objects[cp.planned_destination]
        assert published.metadata == _owner(cp)

        enrich, begin = _quiet()
        with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
            retried = await client.post(f"/api/files/uploads/{cp.id}/retry", headers=_auth(admin_token))
            again = await client.post(f"/api/files/uploads/{cp.id}/retry", headers=_auth(admin_token))
    assert retried.status_code == again.status_code == 200
    assert retried.json()["id"] == again.json()["id"]
    assert retried.json()["storage_uri"] == cp.planned_destination
    assert adapter.count("copy_create_only") == 1
    assert len((await session.execute(select(File))).scalars().all()) == 1
    assert deferred.call_count == 1
    [cp] = await _checkpoints(session)
    assert cp.destination_version == adapter.objects[cp.planned_destination].version


@pytest.mark.asyncio
async def test_a_crash_after_the_file_commits_leaves_the_ingest_copy_for_the_cleanup_pass(
    client, admin_token, raw_storage, session
):
    from app.services import storage_cleanup

    exp = await _experiment(session)
    adapter = _adapter()
    with patch("app.api.files._remove_superseded_copy", AsyncMock()):  # the process stops here
        resp = await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
    assert resp.status_code == 200
    [cp] = await _checkpoints(session)
    assert cp.source_uri in adapter.objects
    file = (await session.execute(select(File))).scalar_one()
    assert file.pending_storage_cleanup["uri"] == cp.source_uri

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        from app import database

        assert await storage_cleanup.reconcile_due(database.async_session_factory) == 1
    assert cp.source_uri not in adapter.objects
    assert adapter.objects[file.storage_uri].data == b"science"


@pytest.mark.asyncio
async def test_an_unrelated_object_at_the_reserved_destination_is_never_claimed(
    client, admin_token, raw_storage, session
):
    exp = await _experiment(session)
    adapter = _adapter()
    # An upload whose reserved destination is already occupied by an untagged object of the same size.
    upload_id = str(uuid.uuid4())
    reserved = uuid.uuid4()
    source = f"gs://bioaf-ingest-test/uploads/{upload_id}/b.txt"
    destination = f"gs://bioaf-raw-test/experiments/{exp.id}/files/{reserved}/b.txt"
    session.add(
        UploadCheckpoint(
            id=upload_id,
            organization_id=exp.organization_id,
            kind="proxied",
            stage="received",
            filename="b.txt",
            source_uri=source,
            experiment_id=exp.id,
            reference="UPL-ABC123",
            file_uuid=reserved,
        )
    )
    await session.commit()
    adapter.put(source, b"science")
    adapter.put(destination, b"science")  # same size, no tags
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        retried = await client.post(f"/api/files/uploads/{upload_id}/retry", headers=_auth(admin_token))
    assert retried.status_code == 202
    cp = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
    assert (cp.stage, cp.last_error_category, cp.next_attempt_at) == ("received", "destination_conflict", None)
    assert adapter.objects[destination].metadata == {} and source in adapter.objects
    assert (await session.execute(select(File))).scalars().all() == []


@pytest.mark.asyncio
async def test_another_uploads_tagged_object_is_never_claimed(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter(copy_failures=1)
    resp = await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
    assert resp.status_code == 202
    [cp] = await _checkpoints(session)
    adapter.put(cp.planned_destination, b"science", {**_owner(cp), "bioaf-upload-id": "another-upload"})
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        retried = await client.post(f"/api/files/uploads/{cp.id}/retry", headers=_auth(admin_token))
    assert retried.status_code == 202
    assert (await _checkpoints(session))[0].last_error_category == "destination_conflict"
    assert adapter.objects[cp.planned_destination].metadata["bioaf-upload-id"] == "another-upload"


@pytest.mark.asyncio
async def test_a_changed_source_is_not_filed(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter(copy_failures=1)
    await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
    [cp] = await _checkpoints(session)
    assert cp.source_version is not None
    adapter.put(cp.source_uri, b"rewritten by someone else")
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        retried = await client.post(f"/api/files/uploads/{cp.id}/retry", headers=_auth(admin_token))
    assert retried.status_code == 202
    assert (await _checkpoints(session))[0].last_error_category == "source_changed"
    assert cp.planned_destination not in adapter.objects


@pytest.mark.asyncio
async def test_filing_does_not_wait_on_its_own_checkpoint_progress(client, admin_token, raw_storage, session):
    """Receipts are committed in the checkpoint's own transaction while the filing transaction is
    open; a dirty checkpoint in the caller's session would make that wait forever."""
    exp = await _experiment(session)
    adapter = _adapter()
    resp = await asyncio.wait_for(_upload(client, admin_token, adapter, exp.id, "a.txt", b"science"), timeout=20)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# legacy checkpoints
# ---------------------------------------------------------------------------


async def _legacy_checkpoint(session, exp, *, legacy_planned: str, source: str | None):
    upload_id = str(uuid.uuid4())
    session.add(
        UploadCheckpoint(
            id=upload_id,
            organization_id=exp.organization_id,
            kind="proxied",
            stage="received",
            filename="same.txt",
            source_uri=source or f"gs://bioaf-ingest-test/uploads/{upload_id}/same.txt",
            planned_destination=legacy_planned,
            experiment_id=exp.id,
            reference="UPL-1E6ACY",
        )
    )
    await session.commit()
    return upload_id


@pytest.mark.asyncio
async def test_a_legacy_checkpoint_with_its_source_is_filed_under_its_own_identity(
    client, admin_token, raw_storage, session
):
    exp = await _experiment(session)
    adapter = _adapter()
    legacy = f"gs://bioaf-raw-test/experiments/{exp.id}/same.txt"
    adapter.put(legacy, b"another file's bytes")
    upload_id = await _legacy_checkpoint(session, exp, legacy_planned=legacy, source=None)
    cp = await session.get(UploadCheckpoint, upload_id)
    adapter.put(cp.source_uri, b"mine")
    enrich, begin = _quiet()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
        resp = await client.post(f"/api/files/uploads/{upload_id}/retry", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    assert "/files/" in resp.json()["storage_uri"]
    assert adapter.objects[resp.json()["storage_uri"]].data == b"mine"
    assert adapter.objects[legacy].data == b"another file's bytes"


@pytest.mark.asyncio
async def test_a_legacy_checkpoint_without_its_source_is_never_adopted(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter()
    legacy = f"gs://bioaf-raw-test/experiments/{exp.id}/same.txt"
    adapter.put(legacy, b"maybe mine, maybe another upload's")
    upload_id = await _legacy_checkpoint(session, exp, legacy_planned=legacy, source=None)
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(f"/api/files/uploads/{upload_id}/retry", headers=_auth(admin_token))
    assert resp.status_code == 202
    cp = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
    assert (cp.stage, cp.last_error_category, cp.planned_destination) == ("received", "legacy_destination", legacy)
    assert (await session.execute(select(File))).scalars().all() == []
    assert adapter.objects[legacy].data == b"maybe mine, maybe another upload's"


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_after_an_interrupted_filing_removes_both_owned_copies(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter()
    adapter.after["copy_create_only"] = lambda s, d: (_ for _ in ()).throw(RuntimeError("lost"))
    resp = await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
    assert resp.status_code == 202
    [cp] = await _checkpoints(session)
    assert cp.planned_destination in adapter.objects and cp.source_uri in adapter.objects

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        cancelled = await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))
    assert cancelled.status_code == 204
    [cp] = await _checkpoints(session)
    assert cp.stage == "cancelled"
    assert cp.planned_destination not in adapter.objects and cp.source_uri not in adapter.objects


@pytest.mark.asyncio
async def test_cancel_never_deletes_an_object_it_does_not_own(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter(copy_failures=1)
    await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
    [cp] = await _checkpoints(session)
    adapter.put(cp.planned_destination, b"science")  # untagged
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))
    assert adapter.objects[cp.planned_destination].data == b"science"
    assert cp.source_uri not in adapter.objects


@pytest.mark.asyncio
async def test_cancelling_is_committed_before_deletion_and_a_storage_failure_is_retried(
    client, admin_token, raw_storage, session
):
    from app import database
    from app.services import upload_settlement

    exp = await _experiment(session)
    adapter = _adapter(copy_failures=1)
    await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
    [cp] = await _checkpoints(session)
    adapter.fail("delete_if_unchanged", 1)
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))
    assert resp.status_code == 204
    [cp] = await _checkpoints(session)
    # Not received (never filed again), not cancelled (bytes remain), not an unconfirmed transfer.
    assert (cp.stage, cp.cancel_from_stage, cp.last_error_category) == ("cancelling", "received", "cancelled")
    assert cp.source_uri in adapter.objects
    await session.execute(text("UPDATE upload_checkpoints SET next_reconcile_at = now() - interval '1 second'"))
    await session.commit()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await upload_settlement.reconcile_due(database.async_session_factory)
    [cp] = await _checkpoints(session)
    assert cp.stage == "cancelled" and cp.source_uri not in adapter.objects


@pytest.mark.asyncio
async def test_a_cancelled_upload_is_never_filed(client, admin_token, raw_storage, session):
    exp = await _experiment(session)
    adapter = _adapter(copy_failures=1)
    await _upload(client, admin_token, adapter, exp.id, "a.txt", b"science")
    [cp] = await _checkpoints(session)
    adapter.fail("delete_if_unchanged", 5)
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))
        retried = await client.post(f"/api/files/uploads/{cp.id}/retry", headers=_auth(admin_token))
    assert retried.status_code in (400, 404, 409)
    assert (await session.execute(select(File))).scalars().all() == []
    from app import database

    await session.execute(text("UPDATE upload_checkpoints SET next_attempt_at = now() - interval '1 second'"))
    await session.commit()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        assert await UploadService.retry_due(database.async_session_factory) == 0


@pytest.mark.asyncio
async def test_an_upload_cancelled_while_its_bytes_arrive_is_never_filed(client, admin_token, raw_storage, session):
    """Review finding 2: the finishing transfer must not turn a cancellation back into a filing."""
    exp = await _experiment(session)
    adapter = _adapter()
    arrived, release = asyncio.Event(), asyncio.Event()
    store_upload = adapter.upload_file.side_effect

    async def slow_upload(uri, source, **kw):
        arrived.set()
        await release.wait()
        await store_upload(uri, source, **kw)

    adapter.upload_file.side_effect = slow_upload

    async def cancel_then_release():
        await arrived.wait()
        [cp] = await _checkpoints(session)
        assert cp.stage == "transferring"
        with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
            resp = await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))
        release.set()
        return resp

    upload, cancel = await asyncio.gather(
        _upload(client, admin_token, adapter, exp.id, "a.txt", b"science"), cancel_then_release()
    )
    assert cancel.status_code == 204
    assert upload.status_code == 409, upload.text
    assert (await session.execute(select(File))).scalars().all() == []
    [cp] = await _checkpoints(session)
    assert cp.stage == "cancelled"
    assert cp.source_uri not in adapter.objects  # the bytes that landed after the cancel are removed


@pytest_asyncio.fixture
async def s3_store(mode_adapter_factory, monkeypatch):
    from app.adapters.storage.s3 import S3StorageProvider
    from app.settings_contract import ComputeMode
    from tests.support.fake_s3 import FakeS3

    adapter = mode_adapter_factory(S3StorageProvider, org_slug="t", mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"
    fake = FakeS3(versioning=True)

    async def creds():
        return None

    monkeypatch.setattr(adapter, "_get_credentials", creds)
    monkeypatch.setattr(adapter, "_get_s3_client", lambda credentials=None: fake)
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        yield adapter, fake


async def _cancelling_s3_upload(session, exp):
    upload_id = str(uuid.uuid4())
    session.add(
        UploadCheckpoint(
            id=upload_id,
            organization_id=exp.organization_id,
            kind="proxied",
            stage="cancelling",
            cancel_from_stage="received",
            last_error_category="cancelled",
            filename="a.txt",
            source_uri=f"s3://ingest/uploads/{upload_id}/a.txt",
            experiment_id=exp.id,
            reference="UPL-5EA5ED",
            file_uuid=uuid.uuid4(),
        )
    )
    await session.commit()
    return upload_id


@pytest.mark.asyncio
async def test_s3_cancellation_removes_older_versions_before_it_settles(session, raw_storage, s3_store):
    """Review finding 5: removing the current version must not leave an older one readable."""
    from app import database
    from app.services.upload_settlement import settle

    adapter, fake = s3_store
    exp = await _experiment(session)
    upload_id = await _cancelling_s3_upload(session, exp)
    key = ("ingest", f"uploads/{upload_id}/a.txt")
    fake.put(*key, b"first attempt")
    fake.put(*key, b"second attempt")

    assert await settle(database.async_session_factory, upload_id) == "cancelled"
    assert key not in fake.objects


@pytest.mark.asyncio
async def test_s3_cancellation_stays_pending_while_an_older_version_is_readable(session, raw_storage, s3_store):
    from app import database
    from app.services.upload_settlement import settle

    adapter, fake = s3_store
    exp = await _experiment(session)
    upload_id = await _cancelling_s3_upload(session, exp)
    key = ("ingest", f"uploads/{upload_id}/a.txt")
    fake.put(*key, b"first attempt")
    fake.put(*key, b"second attempt")
    original = fake.delete_object

    def first_only(**kw):
        fake.delete_object = lambda **k: (_ for _ in ()).throw(RuntimeError("503"))
        return original(**kw)

    fake.delete_object = first_only
    assert await settle(database.async_session_factory, upload_id) == "cancelling"
    assert fake.objects[key].data == b"first attempt"


@pytest.mark.asyncio
async def test_a_cancelled_transfer_that_fails_after_writing_leaves_no_bytes(client, admin_token, raw_storage, session):
    """Review round 2, finding 2: the failure path must settle the cancellation too."""
    exp = await _experiment(session)
    adapter = _adapter()
    arrived, release = asyncio.Event(), asyncio.Event()
    store_upload = adapter.upload_file.side_effect

    async def write_then_fail(uri, source, **kw):
        arrived.set()
        await release.wait()
        await store_upload(uri, source, **kw)
        raise RuntimeError("connection reset after the last byte")

    adapter.upload_file.side_effect = write_then_fail

    async def cancel_then_release():
        await arrived.wait()
        [cp] = await _checkpoints(session)
        with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
            resp = await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))
        release.set()
        return resp

    upload, cancel = await asyncio.gather(
        _upload(client, admin_token, adapter, exp.id, "a.txt", b"science"), cancel_then_release()
    )
    assert cancel.status_code == 204
    assert upload.status_code == 409, upload.text
    [cp] = await _checkpoints(session)
    assert cp.stage == "cancelled"
    assert cp.source_uri not in adapter.objects
