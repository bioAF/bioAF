"""Durable upload recovery and write admission (storage lifecycle 11F; approved D5, mockups M11, M12).

An upload is recorded before any byte may arrive. A failed filing step keeps the received bytes
tracked and retried, never lost and never filed twice. While storage writes are paused, new uploads
are refused before anything is stored.
"""

from __future__ import annotations

import io
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.file import File
from app.models.upload_checkpoint import UploadCheckpoint
from app.services import storage_write_admission
from app.services.upload_service import UploadService

REF = re.compile(r"Reference UPL-[0-9A-F]{6}\.")


def _adapter(move_failures: int = 0):
    from tests.support.uploads import with_upload_session

    adapter = with_upload_session(AsyncMock())
    calls = {"move": 0}

    async def move(src, dst):
        calls["move"] += 1
        if calls["move"] <= move_failures:
            raise RuntimeError("503 POST https://storage.googleapis.com/rewrite SECRET")
        return dst

    adapter.move.side_effect = move
    adapter.exists.return_value = True
    adapter.resolve_uri.side_effect = lambda store, key: f"gs://bioaf-{store.value}-test/{key}"
    adapter.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key.lstrip('/')}")
    adapter.parse_uri = MagicMock(side_effect=lambda uri: (urlparse(uri).netloc, urlparse(uri).path.lstrip("/")))
    adapter.calls = calls
    return adapter


@pytest_asyncio.fixture
async def storage(session, admin_user):
    from app.models.component import PlatformConfig

    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test"))
    session.add(PlatformConfig(key="raw_bucket_name", value="bioaf-raw-test"))
    await session.commit()


async def _experiment(session, name="PBMC pilot"):
    from app.models.experiment import Experiment
    from app.models.user import User

    user = (await session.execute(select(User).limit(1))).scalar_one()
    exp = Experiment(organization_id=user.organization_id, name=name, owner_user_id=user.id, status="registered")
    session.add(exp)
    await session.commit()
    return exp


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _checkpoints(session):
    session.expire_all()
    return (await session.execute(select(UploadCheckpoint))).scalars().all()


async def _simple(client, token, experiment_id, adapter, name="PBMC_S1_L001_R1_001.fastq.gz"):
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        return await client.post(
            f"/api/files/upload/simple?experiment_id={experiment_id}",
            files={"file": (name, io.BytesIO(b"data"), "application/gzip")},
            headers=_auth(token),
        )


# ---------------------------------------------------------------------------
# durable checkpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_direct_upload_is_recorded_before_its_session_exists(client, admin_token, storage, session):
    from app.adapters.models import UploadSessionGrant

    adapter = _adapter()
    seen: list = []

    async def begin(uri, **kw):
        seen.append([cp.stage for cp in await _checkpoints(session)])
        return UploadSessionGrant(protocol="gcs_resumable", handle="https://s/1", url="https://s/1")

    adapter.begin_upload_session.side_effect = begin
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/initiate", json={"filename": "a.fastq.gz"}, headers=_auth(admin_token)
        )
    assert resp.status_code == 200
    assert seen == [["issued"]]
    [cp] = await _checkpoints(session)
    assert cp.id == resp.json()["upload_id"]
    assert (cp.kind, cp.session_handle) == ("session", "https://s/1")


@pytest.mark.asyncio
async def test_a_failed_filing_keeps_the_bytes_and_reports_received_not_filed(client, admin_token, storage, session):
    exp = await _experiment(session)
    adapter = _adapter(move_failures=1)

    resp = await _simple(client, admin_token, exp.id, adapter)

    assert resp.status_code == 202
    body = resp.json()
    assert body["code"] == "upload_received_not_filed"
    assert body["detail"].startswith(
        "Received, not yet filed. Your file is stored safely but could not be added to PBMC pilot yet. "
        "bioAF will try again automatically."
    )
    assert REF.search(body["detail"])
    assert "SECRET" not in resp.text and "googleapis" not in resp.text
    [cp] = await _checkpoints(session)
    assert (cp.stage, cp.attempts, cp.file_id) == ("received", 1, None)
    assert cp.next_attempt_at is not None
    assert (await session.execute(select(File))).scalars().all() == []
    adapter.delete.assert_not_called()


@pytest.mark.asyncio
async def test_the_due_retry_files_it_once(client, admin_token, storage, session):
    from app import database

    exp = await _experiment(session)
    adapter = _adapter(move_failures=1)
    await _simple(client, admin_token, exp.id, adapter)
    await session.execute(text("UPDATE upload_checkpoints SET next_attempt_at = now() - interval '1 second'"))
    await session.commit()

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.upload_service.defer_event") as deferred,
    ):
        filed = await UploadService.retry_due(database.async_session_factory)
        again = await UploadService.retry_due(database.async_session_factory)

    assert (filed, again) == (1, 0)
    files = (await session.execute(select(File))).scalars().all()
    exp_id = exp.id
    assert len(files) == 1 and files[0].experiment_id == exp_id
    file_id = files[0].id
    [cp] = await _checkpoints(session)
    assert (cp.stage, cp.file_id) == ("filed", file_id)
    assert deferred.call_count == 1


@pytest.mark.asyncio
async def test_retries_run_out_then_the_upload_needs_attention(client, admin_token, storage, session):
    exp = await _experiment(session)
    adapter = _adapter(move_failures=99)
    await _simple(client, admin_token, exp.id, adapter)
    upload_id = (await _checkpoints(session))[0].id

    for _ in range(3):
        with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
            resp = await client.post(f"/api/files/uploads/{upload_id}/retry", headers=_auth(admin_token))
    assert resp.status_code == 202
    assert resp.json()["detail"].startswith(
        "Could not be filed. Your file is still stored. Retry now, or cancel the upload to remove it."
    )
    [cp] = await _checkpoints(session)
    assert (cp.attempts, cp.next_attempt_at) == (4, None)

    pending = (await client.get("/api/files/uploads/pending", headers=_auth(admin_token))).json()["uploads"]
    assert [(p["upload_id"], p["state"], p["experiment_name"]) for p in pending] == [
        (upload_id, "needs_attention", "PBMC pilot")
    ]


@pytest.mark.asyncio
async def test_retry_now_files_it_and_a_repeat_returns_the_same_file(client, admin_token, storage, session):
    exp = await _experiment(session)
    adapter = _adapter(move_failures=1)
    await _simple(client, admin_token, exp.id, adapter)
    upload_id = (await _checkpoints(session))[0].id

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        first = await client.post(f"/api/files/uploads/{upload_id}/retry", headers=_auth(admin_token))
        second = await client.post(f"/api/files/uploads/{upload_id}/retry", headers=_auth(admin_token))

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len((await session.execute(select(File))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_concurrent_filing_attempts_register_one_file(session, admin_user, storage):
    """Completion, Retry now and the automatic retry can overlap: only one may file the upload."""
    import asyncio

    from app import database
    from app.services.file_service import FileService
    from app.services.file_verification_service import FileVerificationService

    upload_id = str(uuid.uuid4())
    org_id = admin_user.organization_id
    filename = f"concurrent-{upload_id}.txt"
    session.add(
        UploadCheckpoint(
            id=upload_id,
            organization_id=org_id,
            user_id=admin_user.id,
            kind="proxied",
            stage="received",
            filename=filename,
            reference="UPL-ABCDEF",
            source_uri=f"gs://bioaf-ingest-test/uploads/{upload_id}/{filename}",
            expected_size=3,
        )
    )
    await session.commit()
    entered = 0
    both = asyncio.Event()
    create = FileService.create_file_record

    async def overlapping_create(*args, **kwargs):
        # Without serialization both attempts reach registration together.
        nonlocal entered
        entered += 1
        if entered == 2:
            both.set()
        try:
            await asyncio.wait_for(both.wait(), 1)
        except asyncio.TimeoutError:
            pass
        return await create(*args, **kwargs)

    async def attempt():
        async with database.async_session_factory() as s:
            result = await UploadService.retry_upload(s, org_id, upload_id)
            await s.commit()
            return result.id

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=_adapter()),
        patch.object(FileService, "create_file_record", side_effect=overlapping_create),
        patch.object(FileService, "finalize_registration", AsyncMock()),
        patch.object(FileVerificationService, "begin", AsyncMock()),
        patch("app.services.upload_service.enrich_from_header", AsyncMock()),
        patch("app.services.upload_service.defer_event"),
    ):
        ids = await asyncio.gather(attempt(), attempt())

    assert ids[0] == ids[1]
    assert entered == 1
    count = len((await session.execute(select(File).where(File.filename == filename))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_bytes_an_earlier_attempt_already_moved_are_adopted_not_copied_again(session, admin_user, storage):
    exp = await _experiment(session)
    upload_id = str(uuid.uuid4())
    source = f"gs://bioaf-ingest-test/uploads/{upload_id}/a.fastq.gz"
    from tests.support.uploads import seed_signed_upload

    await seed_signed_upload(
        upload_id=upload_id,
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        filename="a.fastq.gz",
        gcs_uri=source,
        experiment_id=exp.id,
    )
    await session.execute(text("UPDATE upload_checkpoints SET stage='received'"))
    await session.commit()
    adapter = _adapter()
    adapter.exists.side_effect = lambda uri: uri != source  # moved: gone from ingest, present in raw

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        file = await UploadService.retry_upload(session, admin_user.organization_id, upload_id)
        await session.commit()

    assert file.storage_uri.startswith("gs://bioaf-raw-test/")
    assert adapter.calls["move"] == 0


@pytest.mark.asyncio
async def test_cancel_removes_the_stored_copy_and_adds_nothing(client, admin_token, storage, session):
    exp = await _experiment(session)
    adapter = _adapter(move_failures=99)
    await _simple(client, admin_token, exp.id, adapter)
    [cp] = await _checkpoints(session)

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))

    assert resp.status_code == 204
    adapter.delete.assert_awaited_once_with(cp.source_uri)
    [cp] = await _checkpoints(session)
    assert cp.stage == "cancelled"
    assert (await session.execute(select(File))).scalars().all() == []
    audit = (await session.execute(text("SELECT count(*) FROM audit_log WHERE action='upload_cancelled'"))).scalar_one()
    assert audit == 1


@pytest.mark.asyncio
async def test_the_attention_list_shows_bench_only_their_own(
    client, admin_token, bench_token, comp_bio_token, storage, session
):
    exp = await _experiment(session)
    await _simple(client, admin_token, exp.id, _adapter(move_failures=99))

    bench = (await client.get("/api/files/uploads/pending", headers=_auth(bench_token))).json()["uploads"]
    comp_bio = (await client.get("/api/files/uploads/pending", headers=_auth(comp_bio_token))).json()["uploads"]
    assert bench == []
    assert len(comp_bio) == 1


@pytest.mark.asyncio
async def test_someone_elses_upload_cannot_be_retried_or_cancelled_by_bench(
    client, admin_token, bench_token, storage, session
):
    exp = await _experiment(session)
    await _simple(client, admin_token, exp.id, _adapter(move_failures=99))
    [cp] = await _checkpoints(session)
    assert (await client.post(f"/api/files/uploads/{cp.id}/retry", headers=_auth(bench_token))).status_code == 404
    assert (await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(bench_token))).status_code == 404


@pytest.mark.asyncio
async def test_completing_a_filed_upload_again_returns_its_file(session, admin_user, storage):
    from tests.support.uploads import seed_signed_upload

    upload_id = await seed_signed_upload(org_id=admin_user.organization_id, user_id=admin_user.id, filename="x.txt")
    with patch("app.adapters.registry.get_storage_adapter", return_value=_adapter()):
        first = await UploadService.complete_upload(session, admin_user.organization_id, upload_id, "")
        await session.commit()
        second = await UploadService.complete_upload(session, admin_user.organization_id, upload_id, "")
    assert first.id == second.id
    assert len((await session.execute(select(File))).scalars().all()) == 1


# ---------------------------------------------------------------------------
# write admission (D5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paused_storage_refuses_new_uploads_before_anything_is_stored(client, admin_token, storage, session):
    await storage_write_admission.pause_writes(session, operation_id=None)
    await session.commit()
    adapter = _adapter()

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        simple = await client.post(
            "/api/files/upload/simple",
            files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
            headers=_auth(admin_token),
        )
        signed = await client.post("/api/files/upload/initiate", json={"filename": "b.txt"}, headers=_auth(admin_token))

    for resp in (simple, signed):
        assert resp.status_code == 409
        assert resp.json()["code"] == "uploads_paused"
        assert resp.json()["detail"] == (
            "Uploads are paused. An administrator is changing storage. Your files have not been sent. Try again later."
        )
    adapter.upload_file.assert_not_called()
    adapter.generate_signed_url.assert_not_called()
    assert await _checkpoints(session) == []


@pytest.mark.asyncio
async def test_allow_uploads_reopens_them_and_is_audited(client, admin_token, comp_bio_token, storage, session):
    await storage_write_admission.pause_writes(session, operation_id=None)
    await session.commit()

    refused = await client.post("/api/v1/infrastructure/storage/writes/allow", headers=_auth(comp_bio_token))
    allowed = await client.post("/api/v1/infrastructure/storage/writes/allow", headers=_auth(admin_token))

    assert refused.status_code == 403
    assert allowed.status_code == 200 and allowed.json()["paused"] is False
    audit = (
        await session.execute(text("SELECT count(*) FROM audit_log WHERE action='storage_writes_allowed'"))
    ).scalar_one()
    assert audit == 1


@pytest.mark.asyncio
async def test_issued_urls_and_running_transfers_are_outstanding_writers(session, admin_user, storage):
    from app import database
    from tests.support.uploads import seed_signed_upload

    issued = await seed_signed_upload(org_id=admin_user.organization_id, user_id=admin_user.id, filename="x.txt")
    async with storage_write_admission.tracked_transfer("in-flight"):
        writers = await storage_write_admission.outstanding_writers(session)
        remaining = await storage_write_admission.drain(database.async_session_factory, seconds=0.05, interval=0.01)
    assert {w.upload_id for w in writers} == {issued, "in-flight"}
    assert {w.upload_id for w in remaining} == {issued, "in-flight"}

    await session.execute(text("UPDATE upload_checkpoints SET stage='received'"))
    await session.commit()
    assert await storage_write_admission.drain(database.async_session_factory, seconds=0.05, interval=0.01) == []


@pytest.mark.asyncio
async def test_an_expired_signed_url_is_still_an_uncertain_writer(session, admin_user, storage):
    from tests.support.uploads import seed_signed_upload

    await seed_signed_upload(org_id=admin_user.organization_id, user_id=admin_user.id, filename="x.txt")
    await session.execute(text("UPDATE upload_checkpoints SET grant_expires_at = now() - interval '2 hours'"))
    await session.commit()
    assert len(await storage_write_admission.outstanding_writers(session)) == 1


@pytest.mark.asyncio
async def test_a_restart_settles_interrupted_transfers(session, admin_user, storage):
    from app.services.upload_service import _new_checkpoint, reconcile_transfers_on_startup

    for upload_id, stored in (("t-stored", True), ("t-lost", False)):
        await _new_checkpoint(
            id=upload_id,
            organization_id=admin_user.organization_id,
            user_id=admin_user.id,
            kind="proxied",
            stage="transferring",
            filename=f"{upload_id}.txt",
            source_uri=f"gs://bioaf-ingest-test/uploads/{upload_id}/f.txt",
        )
    adapter = _adapter()
    adapter.exists.side_effect = lambda uri: "t-stored" in uri
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        counts = await reconcile_transfers_on_startup(session)
    await session.commit()

    assert counts == {"received": 1, "failed": 1}
    stages = {cp.id: cp.stage for cp in await _checkpoints(session)}
    assert stages == {"t-stored": "received", "t-lost": "failed"}


def test_the_startup_step_is_registered():
    from app import startup

    assert "interrupted_uploads" in [s.name for s in startup.NORMAL_STEPS]


@pytest.mark.asyncio
async def test_lab_document_and_literature_uploads_are_refused_while_paused(session, admin_user, storage):
    from app.services.lab_document_upload_service import LabDocumentUploadService
    from app.services.literature.upload_service import upload_pdf_to_gcs
    from app.services.storage_write_admission import UploadsPausedError

    await storage_write_admission.pause_writes(session, operation_id=None)
    await session.commit()
    with pytest.raises(UploadsPausedError):
        await LabDocumentUploadService.initiate(session, admin_user.organization_id, file_name="sop.pdf")
    with pytest.raises(UploadsPausedError):
        await upload_pdf_to_gcs(session, paper_id=1, pdf_bytes=b"%PDF")


# ---------------------------------------------------------------------------
# store health before anything is written or signed (11F)
# ---------------------------------------------------------------------------

UPLOAD_STORAGE_UNAVAILABLE = (
    "Uploads are unavailable because bioAF cannot reach its upload storage. Your files have not been sent. "
    "Ask an administrator to check Storage under Infrastructure > Components."
)


def _ingest_health(health):
    from app.services import storage_identity
    from app.services.storage_identity import StoreReport

    async def inspect(session, store, bucket):
        return StoreReport(store, bucket, health)

    return patch.object(storage_identity, "inspect_bucket", side_effect=inspect)


@pytest.mark.parametrize("health", ["not_found", "no_access", "wrong_owner"])
@pytest.mark.asyncio
async def test_an_unusable_upload_store_refuses_before_anything_is_sent(client, admin_token, storage, session, health):
    from app.services.storage_identity import StoreHealth

    exp = await _experiment(session)
    adapter = _adapter()
    with _ingest_health(StoreHealth(health)), patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        signed = await client.post(
            "/api/files/upload/initiate",
            json={"filename": "a.txt", "experiment_id": exp.id, "expected_size_bytes": 3},
            headers=_auth(admin_token),
        )
        proxied = await client.post(
            f"/api/files/upload/simple?experiment_id={exp.id}",
            files={"file": ("a.txt", io.BytesIO(b"abc"), "text/plain")},
            headers=_auth(admin_token),
        )

    for response in (signed, proxied):
        assert response.status_code == 409
        assert response.json()["code"] == "upload_storage_unavailable"
        assert response.json()["detail"] == UPLOAD_STORAGE_UNAVAILABLE
    assert await _checkpoints(session) == []
    adapter.generate_signed_url.assert_not_awaited()
    adapter.upload_file.assert_not_awaited()


@pytest.mark.parametrize("health", ["not_confirmed", "cannot_check", "not_responding"])
@pytest.mark.asyncio
async def test_a_store_that_is_not_known_to_be_unusable_still_accepts_uploads(
    client, admin_token, storage, session, health
):
    from app.services.storage_identity import StoreHealth

    exp = await _experiment(session)
    with _ingest_health(StoreHealth(health)):
        response = await _simple(client, admin_token, exp.id, _adapter())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_lab_document_and_literature_uploads_check_their_store(session, admin_user, storage):
    from app.models.component import PlatformConfig
    from app.services.lab_document_upload_service import LabDocumentUploadService
    from app.services.literature.upload_service import upload_pdf_to_gcs
    from app.services.storage_identity import StoreHealth
    from app.services.storage_write_admission import UploadStorageUnavailableError

    session.add(PlatformConfig(key="working_bucket_name", value="bioaf-working-test"))
    session.add(PlatformConfig(key="literature_bucket_name", value="bioaf-literature-test"))
    await session.commit()
    cloud = MagicMock(is_local=False, named_containers=True)
    with _ingest_health(StoreHealth.NOT_FOUND), patch("app.adapters.registry.get_storage_adapter", return_value=cloud):
        with pytest.raises(UploadStorageUnavailableError):
            await LabDocumentUploadService.initiate(session, admin_user.organization_id, file_name="sop.pdf")
        with pytest.raises(UploadStorageUnavailableError):
            await upload_pdf_to_gcs(session, paper_id=1, pdf_bytes=b"%PDF")


@pytest.mark.asyncio
async def test_a_simulated_store_is_never_refused_for_being_absent(session, storage):
    """Local mode creates a store's directory on first write: absent is not unusable there."""
    from app.services.storage_write_admission import require_store_usable

    adapter = MagicMock()
    adapter.is_local = True
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await require_store_usable(session, "ingest")
    adapter.inspect_container.assert_not_called()


def test_the_received_not_filed_receipt_is_part_of_the_api_contract():
    """Every route that can answer 202 received-not-filed declares it, so clients type it (M12)."""
    from app.main import create_app

    schema = create_app().openapi()
    for path in ("/api/files/upload/complete", "/api/files/upload/simple", "/api/files/uploads/{upload_id}/retry"):
        accepted = schema["paths"][path]["post"]["responses"]["202"]
        ref = accepted["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/UploadReceivedNotFiled"), path
    receipt = schema["components"]["schemas"]["UploadReceivedNotFiled"]
    assert receipt["properties"]["code"]["const"] == "upload_received_not_filed"
    assert set(schema["components"]["schemas"]["UploadReceivedNotFiledDetails"]["required"]) == {
        "upload_id",
        "reference",
    }


# ---------------------------------------------------------------------------
# explicit outcome: leaving the pending list does not mean filed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_uploads_outcome_tells_filed_from_cancelled(client, admin_token, storage, session):
    exp = await _experiment(session)
    adapter = _adapter(move_failures=99)
    await _simple(client, admin_token, exp.id, adapter)
    [cp] = await _checkpoints(session)

    waiting = (await client.get(f"/api/files/uploads/{cp.id}", headers=_auth(admin_token))).json()
    assert (waiting["outcome"], waiting["state"], waiting["file_id"]) == ("pending", "retrying", None)

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await client.post(f"/api/files/uploads/{cp.id}/cancel", headers=_auth(admin_token))
    cancelled = (await client.get(f"/api/files/uploads/{cp.id}", headers=_auth(admin_token))).json()
    assert (cancelled["outcome"], cancelled["file_id"]) == ("cancelled", None)


@pytest.mark.asyncio
async def test_a_filed_uploads_outcome_names_its_file(client, admin_token, storage, session):
    exp = await _experiment(session)
    adapter = _adapter(move_failures=1)
    await _simple(client, admin_token, exp.id, adapter)
    [cp] = await _checkpoints(session)
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        filed = (await client.post(f"/api/files/uploads/{cp.id}/retry", headers=_auth(admin_token))).json()

    outcome = (await client.get(f"/api/files/uploads/{cp.id}", headers=_auth(admin_token))).json()
    assert (outcome["outcome"], outcome["file_id"]) == ("filed", filed["id"])


@pytest.mark.asyncio
async def test_a_failed_uploads_outcome_is_failed(client, admin_token, storage, session):
    exp = await _experiment(session)
    await _simple(client, admin_token, exp.id, _adapter(move_failures=99))
    [cp] = await _checkpoints(session)
    await session.execute(text("UPDATE upload_checkpoints SET stage='failed' WHERE id=:i").bindparams(i=cp.id))
    await session.commit()
    outcome = (await client.get(f"/api/files/uploads/{cp.id}", headers=_auth(admin_token))).json()
    assert outcome["outcome"] == "failed"


@pytest.mark.asyncio
async def test_someone_elses_upload_outcome_is_not_found_for_bench(client, admin_token, bench_token, storage, session):
    exp = await _experiment(session)
    await _simple(client, admin_token, exp.id, _adapter(move_failures=99))
    [cp] = await _checkpoints(session)
    assert (await client.get(f"/api/files/uploads/{cp.id}", headers=_auth(bench_token))).status_code == 404
