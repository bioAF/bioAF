"""TDD: ReferenceDataService.upload_complete() and abort_upload() — spec §2.

After the browser has PUT all bytes to GCS, `upload_complete` lists the bucket
prefix, verifies each declared file arrived, reads the GCS-reported md5 hash
and size, persists `reference_dataset_files` rows, builds the md5 manifest,
and flips status:
  - internal -> 'active'
  - public   -> 'pending_approval' (initial public uploads require admin approval)

`abort_upload` deletes any uploaded GCS objects and the dataset row.

GCS calls are stubbed via monkeypatching `_create_resumable_session`,
`_list_uploaded_blobs`, and `_delete_blobs`.
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.reference_dataset import ReferenceDataset, ReferenceDatasetFile
from app.schemas.reference_dataset import (
    ReferenceUploadFileSpec,
    ReferenceUploadInitRequest,
)
from app.services.auth_service import AuthService
from app.services.reference_data_service import ReferenceDataService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_uploadcomplete@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def configured_refs_bucket(session):
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('references_bucket_name', 'bioaf-references-test', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()


def _stub_session_url(bucket_name, blob_path, content_type, size_bytes, origin=None, credentials=None):
    return f"https://storage.googleapis.com/stub/{blob_path}"


class _StubBlob:
    def __init__(self, name: str, size: int, md5: str):
        self.name = name
        self.size = size
        self.md5_hash = md5


async def _init(session, user, **overrides):
    payload = ReferenceUploadInitRequest(
        name=overrides.pop("name", "Custom Markers"),
        category=overrides.pop("category", "markers"),
        scope=overrides.pop("scope", "internal"),
        version=overrides.pop("version", "v1"),
        files=[
            ReferenceUploadFileSpec(filename="markers.csv", size_bytes=4096),
            ReferenceUploadFileSpec(filename="metadata.json", size_bytes=512),
        ],
    )
    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        dataset, _ = await ReferenceDataService.init_upload(
            session, org_id=user.organization_id, user_id=user.id, request=payload
        )
        await session.commit()
    return dataset


@pytest.mark.asyncio
async def test_init_upload_persists_skeleton_file_rows(session, comp_bio_user, configured_refs_bucket):
    """init_upload must persist a ReferenceDatasetFile row per declared file."""
    dataset = await _init(session, comp_bio_user)

    rows = await session.execute(
        select(ReferenceDatasetFile).where(ReferenceDatasetFile.reference_dataset_id == dataset.id)
    )
    files = list(rows.scalars().all())
    assert len(files) == 2
    filenames = {f.filename for f in files}
    assert filenames == {"markers.csv", "metadata.json"}
    # md5 not yet known; size is the client-declared expected size
    for f in files:
        assert f.md5_checksum is None
        assert f.gcs_uri.startswith("gs://bioaf-references-test/")


@pytest.mark.asyncio
async def test_upload_complete_internal_flips_to_active(session, comp_bio_user, configured_refs_bucket):
    dataset = await _init(session, comp_bio_user)

    blobs = [
        _StubBlob(name=f"{dataset.gcs_prefix}markers.csv", size=4096, md5="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        _StubBlob(name=f"{dataset.gcs_prefix}metadata.json", size=512, md5="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    ]
    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=blobs):
        result = await ReferenceDataService.upload_complete(
            session, reference_id=dataset.id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
        )
        await session.commit()

    assert result.status == "active"
    assert result.total_size_bytes == 4096 + 512
    assert result.md5_manifest_json is not None
    assert result.md5_manifest_json["markers.csv"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    rows = await session.execute(
        select(ReferenceDatasetFile).where(ReferenceDatasetFile.reference_dataset_id == dataset.id)
    )
    files = {f.filename: f for f in rows.scalars().all()}
    assert files["markers.csv"].md5_checksum == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert files["markers.csv"].size_bytes == 4096

    audit = await session.execute(
        text(
            "SELECT * FROM audit_log WHERE entity_type = 'reference_dataset' "
            "AND entity_id = :id AND action = 'upload_completed'"
        ),
        {"id": dataset.id},
    )
    assert audit.fetchone() is not None


@pytest.mark.asyncio
async def test_upload_complete_public_goes_pending_approval(session, comp_bio_user, configured_refs_bucket):
    dataset = await _init(session, comp_bio_user, name="GENCODE", category="genome", scope="public", version="v45")

    blobs = [
        _StubBlob(name=f"{dataset.gcs_prefix}markers.csv", size=4096, md5="aaaa1111aaaa1111aaaa1111aaaa1111"),
        _StubBlob(name=f"{dataset.gcs_prefix}metadata.json", size=512, md5="bbbb2222bbbb2222bbbb2222bbbb2222"),
    ]
    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=blobs):
        result = await ReferenceDataService.upload_complete(
            session, reference_id=dataset.id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
        )

    assert result.status == "pending_approval"


@pytest.mark.asyncio
async def test_upload_complete_missing_file_keeps_status_uploading(session, comp_bio_user, configured_refs_bucket):
    dataset = await _init(session, comp_bio_user)

    # Only one of the two declared files is present in GCS.
    blobs = [
        _StubBlob(name=f"{dataset.gcs_prefix}markers.csv", size=4096, md5="aa" * 16),
    ]
    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=blobs):
        with pytest.raises(ValueError, match="metadata.json"):
            await ReferenceDataService.upload_complete(
                session, reference_id=dataset.id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
            )

    # status must remain 'uploading' so the caller can retry once they finish PUTs.
    fresh = await session.get(ReferenceDataset, dataset.id)
    assert fresh.status == "uploading"


@pytest.mark.asyncio
async def test_upload_complete_client_md5_mismatch_marks_failed(session, comp_bio_user, configured_refs_bucket):
    """Advanced path: caller provided expected md5; mismatch => status=failed."""
    payload = ReferenceUploadInitRequest(
        name="Strict Markers",
        category="markers",
        scope="internal",
        version="v1",
        files=[
            ReferenceUploadFileSpec(filename="markers.csv", size_bytes=4096, md5_checksum="cc" * 16),
        ],
    )
    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        dataset, _ = await ReferenceDataService.init_upload(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    # Bucket-reported md5 differs from the client-declared one
    blobs = [_StubBlob(name=f"{dataset.gcs_prefix}markers.csv", size=4096, md5="dd" * 16)]
    with (
        patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=blobs),
        patch.object(ReferenceDataService, "_delete_blobs", return_value=None),
    ):
        with pytest.raises(ValueError, match="md5 mismatch"):
            await ReferenceDataService.upload_complete(
                session, reference_id=dataset.id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
            )
        await session.commit()

    fresh = await session.get(ReferenceDataset, dataset.id)
    assert fresh.status == "failed"
    assert "md5 mismatch" in (fresh.deprecation_note or "").lower()


@pytest.mark.asyncio
async def test_upload_complete_only_works_in_uploading_status(session, comp_bio_user, configured_refs_bucket):
    dataset = await _init(session, comp_bio_user)
    blobs = [
        _StubBlob(name=f"{dataset.gcs_prefix}markers.csv", size=4096, md5="aa" * 16),
        _StubBlob(name=f"{dataset.gcs_prefix}metadata.json", size=512, md5="bb" * 16),
    ]
    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=blobs):
        await ReferenceDataService.upload_complete(
            session, reference_id=dataset.id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
        )
        await session.commit()

        with pytest.raises(ValueError, match="already finalized|cannot finalize|status"):
            await ReferenceDataService.upload_complete(
                session, reference_id=dataset.id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
            )


@pytest.mark.asyncio
async def test_abort_upload_deletes_blobs_and_row(session, comp_bio_user, configured_refs_bucket):
    dataset = await _init(session, comp_bio_user)
    dataset_id = dataset.id

    deleted_calls: list[tuple[str, str]] = []

    def _capture_delete(bucket_name, prefix, credentials=None):
        deleted_calls.append((bucket_name, prefix))

    with patch.object(ReferenceDataService, "_delete_blobs", side_effect=_capture_delete):
        await ReferenceDataService.abort_upload(
            session, reference_id=dataset_id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
        )
        await session.commit()

    assert deleted_calls == [("bioaf-references-test", dataset.gcs_prefix)]
    fresh = await session.get(ReferenceDataset, dataset_id)
    assert fresh is None


@pytest.mark.asyncio
async def test_abort_upload_idempotent_on_missing_row(session, comp_bio_user, configured_refs_bucket):
    """Aborting a reference that doesn't exist should not error (idempotent per spec §2)."""
    with patch.object(ReferenceDataService, "_delete_blobs", return_value=None):
        # Should not raise even when row is gone
        await ReferenceDataService.abort_upload(
            session, reference_id=999_999, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
        )
