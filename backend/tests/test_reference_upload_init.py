"""TDD: ReferenceDataService.init_upload() — spec §2 Upload flow.

The init_upload service method:
- creates a ReferenceDataset row in status='uploading' (no files persisted yet)
- requests a GCS resumable upload session for each declared file
- returns the session URLs to the caller (browser will PUT directly to GCS)
- writes an audit log entry
- enforces (org_id, name, version) uniqueness up-front

GCS calls are stubbed via monkeypatching `_create_resumable_session`.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text

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
        email="compbio_uploadinit@test.com",
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
    """Seed platform_config with a references bucket name."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('references_bucket_name', 'bioaf-references-test', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()
    return "bioaf-references-test"


def _stub_session_url(bucket_name, blob_path, content_type, size_bytes, origin=None, credentials=None):
    """Drop-in replacement for the live GCS resumable session creator."""
    return (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket_name}/o"
        f"?uploadType=resumable&upload_id=stub-{blob_path.replace('/', '-')}"
    )


@pytest.mark.asyncio
async def test_init_upload_creates_uploading_dataset(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceUploadInitRequest(
        name="GRCh38 GENCODE",
        category="genome",
        scope="public",
        version="v45",
        source_url="https://www.gencodegenes.org/human/release_45.html",
        files=[
            ReferenceUploadFileSpec(filename="genome.fa", size_bytes=2_500_000_000),
            ReferenceUploadFileSpec(filename="genes.gtf", size_bytes=500_000_000),
        ],
    )

    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        dataset, uploads = await ReferenceDataService.init_upload(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )

    assert dataset.id is not None
    assert dataset.status == "uploading"
    assert dataset.name == "GRCh38 GENCODE"
    assert dataset.version == "v45"
    assert dataset.uploaded_by_user_id == comp_bio_user.id
    # gcs_prefix should namespace by category + slug(name) + slug(version) and end in /
    assert dataset.gcs_prefix.endswith("/")
    assert "v45" in dataset.gcs_prefix
    assert "grch38-gencode" in dataset.gcs_prefix.lower()

    assert len(uploads) == 2
    filenames = {u["filename"] for u in uploads}
    assert filenames == {"genome.fa", "genes.gtf"}
    for u in uploads:
        assert u["session_url"].startswith("https://storage.googleapis.com/")
        # expires_at must be a datetime-or-isoformat string in the future
        expires = u["expires_at"]
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires)
        assert expires > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_init_upload_rejects_duplicate_name_version(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceUploadInitRequest(
        name="GRCh38 GENCODE",
        category="genome",
        scope="public",
        version="v45",
        files=[ReferenceUploadFileSpec(filename="genome.fa", size_bytes=100)],
    )

    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        await ReferenceDataService.init_upload(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

        with pytest.raises(ValueError, match="already exists"):
            await ReferenceDataService.init_upload(
                session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
            )


@pytest.mark.asyncio
async def test_init_upload_writes_audit_log(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceUploadInitRequest(
        name="Custom Markers",
        category="markers",
        scope="internal",
        version="v1",
        files=[ReferenceUploadFileSpec(filename="markers.csv", size_bytes=4096)],
    )

    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        dataset, _ = await ReferenceDataService.init_upload(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    result = await session.execute(
        text(
            "SELECT user_id, action FROM audit_log "
            "WHERE entity_type = 'reference_dataset' "
            "AND entity_id = :id AND action = 'upload_initiated'"
        ),
        {"id": dataset.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row.user_id == comp_bio_user.id


@pytest.mark.asyncio
async def test_init_upload_raises_when_bucket_not_configured(session, comp_bio_user):
    """If platform_config has no references_bucket_name, the call must fail clearly."""
    # Defensive: scrub the key in case prior tests inserted it.
    await session.execute(text("DELETE FROM platform_config WHERE key = 'references_bucket_name'"))
    await session.commit()

    payload = ReferenceUploadInitRequest(
        name="No Bucket",
        category="other",
        scope="internal",
        version="v1",
        files=[ReferenceUploadFileSpec(filename="x.txt", size_bytes=1)],
    )

    with pytest.raises(ValueError, match="not configured"):
        await ReferenceDataService.init_upload(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )


@pytest.mark.asyncio
async def test_init_upload_requires_at_least_one_file(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceUploadInitRequest(
        name="Empty",
        category="other",
        scope="internal",
        version="v1",
        files=[],
    )

    with pytest.raises(ValueError, match="at least one file"):
        await ReferenceDataService.init_upload(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
