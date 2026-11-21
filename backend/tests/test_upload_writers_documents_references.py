"""Lab document and reference dataset uploads are writers too (storage lifecycle 11G; review gaps).

Both send bytes straight to a bucket Destroy Storage empties, through a provider session the
browser holds. Each is recorded as an upload checkpoint for its purpose, so it counts as a
writer, survives a restart, and settles the same way as a file upload: when it is placed or
finalized, or when the provider confirms it ended. A finished session nobody claimed stops
counting as a writer but is never filed as a data file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.adapters.models import UploadSessionState, UploadSessionStatus
from app.models.upload_checkpoint import UploadCheckpoint
from app.services import storage_write_admission

UPLOAD = "app.services.lab_document_upload_service.LabDocumentUploadService"
GCS_SESSION = "https://storage.googleapis.com/upload/storage/v1/b/w/o?uploadType=resumable&upload_id=LAB"
S3_PUT = "https://bioaf-working.s3.amazonaws.com/lab?X-Amz-Signature=abc"


@pytest_asyncio.fixture
async def buckets(session):
    for key, value in {
        "working_bucket_name": "bioaf-working-test",
        "references_bucket_name": "bioaf-references-test",
    }.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


async def _checkpoints(session, purpose):
    session.expire_all()
    return (await session.execute(select(UploadCheckpoint).where(UploadCheckpoint.purpose == purpose))).scalars().all()


async def _writers(session):
    return {w.upload_id for w in await storage_write_admission.outstanding_writers(session)}


async def _initiate_lab(session, admin_user, url=GCS_SESSION):
    from app.services.lab_document_upload_service import LabDocumentUploadService

    with patch(f"{UPLOAD}._create_resumable_session", new_callable=AsyncMock, return_value=url):
        return await LabDocumentUploadService.initiate(
            session, admin_user.organization_id, file_name="sop.pdf", user_id=admin_user.id
        )


@pytest.mark.asyncio
async def test_a_lab_document_session_is_a_writer_until_it_is_placed(session, admin_user, buckets):
    from app.services.lab_document_upload_service import LabDocumentUploadService

    org_id = admin_user.organization_id
    result = await _initiate_lab(session, admin_user)
    [cp] = await _checkpoints(session, "lab_document")
    assert (cp.id, cp.kind, cp.stage, cp.session_handle) == (result["upload_token"], "session", "issued", GCS_SESSION)
    assert await _writers(session) == {cp.id}

    adapter = AsyncMock()
    adapter.build_uri = lambda bucket, key: f"gs://{bucket}/{key}"
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await LabDocumentUploadService.place(
            session, upload_token=result["upload_token"], org_id=org_id, document_id=7, version=1
        )
    await session.commit()
    [cp] = await _checkpoints(session, "lab_document")
    assert cp.stage == "filed"
    assert await _writers(session) == set()


@pytest.mark.asyncio
async def test_a_presigned_lab_document_link_settles_like_any_presigned_upload(session, admin_user, buckets):
    await _initiate_lab(session, admin_user, url=S3_PUT)
    [cp] = await _checkpoints(session, "lab_document")
    assert cp.kind == "signed"
    assert cp.grant_expires_at is not None


@pytest.mark.asyncio
async def test_a_finished_lab_document_nobody_placed_stops_counting_and_is_never_filed(
    session, admin_user, buckets, client, admin_token
):
    from app import database
    from app.services.upload_settlement import reconcile_due

    await _initiate_lab(session, admin_user)
    await session.execute(text("UPDATE upload_checkpoints SET next_reconcile_at = now() - interval '1 second'"))
    await session.commit()
    adapter = AsyncMock()
    adapter.inspect_upload_session.return_value = UploadSessionStatus(state=UploadSessionState.COMPLETED)
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await reconcile_due(database.async_session_factory)

    [cp] = await _checkpoints(session, "lab_document")
    assert (cp.stage, cp.next_attempt_at) == ("received", None)
    assert await _writers(session) == set()
    listed = (await client.get("/api/files/uploads/pending", headers={"Authorization": f"Bearer {admin_token}"})).json()
    assert listed["uploads"] == []


# ---------------------------------------------------------------------------
# reference datasets
# ---------------------------------------------------------------------------


def _stub_session_url(bucket_name, blob_path, content_type, size_bytes, origin=None, credentials=None):
    return f"https://storage.googleapis.com/upload?uploadType=resumable&upload_id=ref-{blob_path.replace('/', '-')}"


def _request():
    from app.schemas.reference_dataset import ReferenceUploadFileSpec, ReferenceUploadInitRequest

    return ReferenceUploadInitRequest(
        name="GRCh38",
        category="genome",
        scope="internal",
        version="v45",
        files=[
            ReferenceUploadFileSpec(filename="genome.fa", size_bytes=10),
            ReferenceUploadFileSpec(filename="genes.gtf", size_bytes=5),
        ],
    )


async def _init_reference(session, admin_user):
    from app.services.reference_data_service import ReferenceDataService

    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        dataset, _ = await ReferenceDataService.init_upload(
            session, admin_user.organization_id, admin_user.id, _request()
        )
    await session.commit()
    return dataset


@pytest.mark.asyncio
async def test_each_reference_file_session_is_a_writer(session, admin_user, buckets):
    await _init_reference(session, admin_user)
    cps = await _checkpoints(session, "reference")
    assert sorted(cp.filename for cp in cps) == ["genes.gtf", "genome.fa"]
    assert {cp.kind for cp in cps} == {"session"}
    assert await _writers(session) == {cp.id for cp in cps}


@pytest.mark.asyncio
async def test_reference_uploads_are_refused_while_storage_writes_are_paused(session, admin_user, buckets):
    from app.services.storage_write_admission import UploadsPausedError

    await storage_write_admission.pause_writes(session, operation_id=None)
    await session.commit()
    with pytest.raises(UploadsPausedError):
        await _init_reference(session, admin_user)
    assert await _checkpoints(session, "reference") == []


@pytest.mark.asyncio
async def test_aborting_a_reference_upload_cancels_its_sessions_with_the_provider(session, admin_user, buckets):
    from app.services.reference_data_service import ReferenceDataService

    dataset = await _init_reference(session, admin_user)
    with patch.object(ReferenceDataService, "_delete_blobs"):
        await ReferenceDataService.abort_upload(session, dataset.id, admin_user.organization_id, admin_user.id)
    await session.commit()

    cps = await _checkpoints(session, "reference")
    assert {cp.stage for cp in cps} == {"cancelling"}
    assert await _writers(session) == {cp.id for cp in cps}


@pytest.mark.asyncio
async def test_finalizing_a_reference_upload_settles_its_sessions(session, admin_user, buckets):
    from app.services.reference_data_service import ReferenceDataService

    org_id = admin_user.organization_id
    dataset_id = (await _init_reference(session, admin_user)).id
    dataset = await ReferenceDataService.get_reference(session, dataset_id, org_id)
    await ReferenceDataService.settle_upload_sessions(session, dataset, stage="filed")
    await session.commit()
    assert {cp.stage for cp in await _checkpoints(session, "reference")} == {"filed"}
    assert await _writers(session) == set()
