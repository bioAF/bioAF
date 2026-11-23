"""Stable file identity and the organized object layout (project 12B.1a, approved D1).

Organized raw objects live under the File's permanent UUID, so two files with one display name
never share an object: ``experiments/{experiment_id}/files/{file_uuid}/{filename}`` and
``unlinked/files/{file_uuid}/{filename}``. An upload reserves that UUID durably before its first
filing attempt and every attempt registers the File under it. Unassigned intake keeps its unique
``uploads/{upload_id}/{filename}`` URI.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.exceptions import ValidationError
from app.models.file import File
from app.models.upload_checkpoint import UploadCheckpoint
from app.services.file_organization import organized_key, validate_object_filename
from tests.test_upload_recovery import _adapter, _auth, _checkpoints


@pytest_asyncio.fixture
async def storage(session, admin_user):
    from app.models.component import PlatformConfig

    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test"))
    session.add(PlatformConfig(key="raw_bucket_name", value="bioaf-raw-test"))
    await session.commit()


U = uuid.UUID("0f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a")


# ---------------------------------------------------------------------------
# canonical layout
# ---------------------------------------------------------------------------


def test_an_experiment_file_is_organized_under_its_uuid():
    assert organized_key(U, 7, "reads.fastq.gz") == f"experiments/7/files/{U}/reads.fastq.gz"


def test_an_unlinked_file_is_organized_under_its_uuid():
    assert organized_key(U, None, "reads.fastq.gz") == f"unlinked/files/{U}/reads.fastq.gz"


def test_two_files_with_one_name_get_different_keys():
    other = uuid.uuid4()
    assert organized_key(U, 7, "same.txt") != organized_key(other, 7, "same.txt")


def test_the_layout_requires_an_identity():
    with pytest.raises((TypeError, ValueError)):
        organized_key(None, 7, "a.txt")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "a/b.txt", "../x", "a\\b.txt", "nul\0.txt", "line\nbreak.txt", "tab\t.txt", "del\x7f.txt"],
)
def test_an_unsafe_filename_is_refused(name):
    with pytest.raises(ValidationError):
        validate_object_filename(name)


@pytest.mark.parametrize(
    "name", ["reads.fastq.gz", "PBMC S1 (copy).txt", "a[1]*?.txt", "échantillon_é.csv", ".hidden", "x" * 255]
)
def test_an_ordinary_filename_is_kept_exactly(name):
    assert validate_object_filename(name) == name


def test_a_filename_too_long_for_one_key_segment_is_refused():
    with pytest.raises(ValidationError):
        validate_object_filename("é" * 200)  # 400 bytes of UTF-8


def test_the_layout_refuses_an_unsafe_filename():
    with pytest.raises(ValidationError):
        organized_key(U, 7, "../../escape.txt")


# ---------------------------------------------------------------------------
# admission: an unsafe filename is refused before anything is recorded or stored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_refuses_an_unsafe_filename_before_any_session(client, admin_token, storage, session):
    adapter = _adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/initiate", json={"filename": "../evil.fastq.gz"}, headers=_auth(admin_token)
        )
    assert resp.status_code == 400
    assert await _checkpoints(session) == []
    adapter.begin_upload_session.assert_not_called()


@pytest.mark.asyncio
async def test_a_proxied_upload_refuses_an_unsafe_filename_before_any_write(client, admin_token, storage, session):
    adapter = _adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("a\\b.txt", io.BytesIO(b"data"), "text/plain")},
            headers=_auth(admin_token),
        )
    assert resp.status_code == 400
    assert await _checkpoints(session) == []
    adapter.upload_file.assert_not_called()


# ---------------------------------------------------------------------------
# durable reservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_direct_upload_reserves_a_file_uuid_when_it_is_recorded(client, admin_token, storage, session):
    adapter = _adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post("/api/files/upload/initiate", json={"filename": "a.txt"}, headers=_auth(admin_token))
    assert resp.status_code == 200
    [cp] = await _checkpoints(session)
    assert isinstance(cp.file_uuid, uuid.UUID)


@pytest.mark.asyncio
async def test_two_checkpoints_cannot_reserve_one_file_uuid(session, admin_user):
    for n in range(2):
        session.add(
            UploadCheckpoint(
                id=str(uuid.uuid4()),
                organization_id=admin_user.organization_id,
                kind="proxied",
                stage="received",
                filename="a.txt",
                source_uri=f"gs://b/uploads/{n}/a.txt",
                reference=f"UPL-00000{n}",
                file_uuid=U,
            )
        )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.asyncio
async def test_a_non_file_checkpoint_needs_no_reservation(session, admin_user):
    session.add(
        UploadCheckpoint(
            id=str(uuid.uuid4()),
            organization_id=admin_user.organization_id,
            purpose="lab_document",
            kind="signed",
            stage="issued",
            filename="sop.pdf",
            source_uri="gs://b/lab-docs/sop.pdf",
            reference="UPL-ABCDEF",
        )
    )
    await session.flush()
    assert (await session.execute(select(UploadCheckpoint))).scalar_one().file_uuid is None
    await session.rollback()


@pytest.mark.asyncio
async def test_a_file_record_takes_a_reserved_uuid_and_otherwise_generates_one(session, admin_user):
    from app.services.file_service import FileService

    reserved = await FileService.create_file_record(
        session,
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        filename="a.txt",
        storage_uri="gs://b/a.txt",
        size_bytes=1,
        md5_checksum=None,
        file_type="other",
        file_uuid=U,
    )
    default = await FileService.create_file_record(
        session,
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        filename="b.txt",
        storage_uri="gs://b/b.txt",
        size_bytes=1,
        md5_checksum=None,
        file_type="other",
    )
    await session.flush()
    await session.refresh(default)
    assert reserved.uuid == U
    assert isinstance(default.uuid, uuid.UUID) and default.uuid != U
    await session.rollback()


def _no_side_effects():
    return (
        patch("app.services.upload_service.enrich_from_header", AsyncMock()),
        patch("app.services.file_verification_service.FileVerificationService.begin", AsyncMock()),
    )


@pytest.mark.asyncio
async def test_an_unassigned_upload_is_filed_under_its_reserved_uuid_at_its_intake_uri(
    client, admin_token, storage, session
):
    adapter = _adapter()
    enrich, begin = _no_side_effects()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("same.txt", io.BytesIO(b"data"), "text/plain")},
            headers=_auth(admin_token),
        )
    assert resp.status_code == 200, resp.text
    [cp] = await _checkpoints(session)
    file = (await session.execute(select(File))).scalar_one()
    assert file.uuid == cp.file_uuid
    # Intake is unchanged: the unique ingest URI, no move, no raw-bucket dependency.
    assert file.storage_uri == f"gs://bioaf-ingest-test/uploads/{cp.id}/same.txt"
    adapter.move.assert_not_called()
    adapter.copy.assert_not_called()


@pytest.mark.asyncio
async def test_a_retried_filing_registers_the_same_reserved_uuid(client, admin_token, storage, session):
    from app.services.upload_service import UploadService

    adapter = _adapter()
    enrich, begin = _no_side_effects()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
        with patch(
            "app.services.file_service.FileService.finalize_registration", AsyncMock(side_effect=RuntimeError("x"))
        ):
            resp = await client.post(
                "/api/files/upload/simple",
                files={"file": ("a.txt", io.BytesIO(b"data"), "text/plain")},
                headers=_auth(admin_token),
            )
        assert resp.status_code == 202, resp.text
        [cp] = await _checkpoints(session)
        reserved = cp.file_uuid
        assert reserved is not None
        org_id = cp.organization_id
        file = await UploadService.retry_upload(session, org_id, cp.id)
        await session.commit()
    assert file.uuid == reserved
    assert len((await session.execute(select(File))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_a_legacy_checkpoint_without_a_reservation_gets_one_before_filing(session, admin_user, storage):
    from app.services.upload_service import UploadService

    upload_id = str(uuid.uuid4())
    session.add(
        UploadCheckpoint(
            id=upload_id,
            organization_id=admin_user.organization_id,
            user_id=admin_user.id,
            kind="proxied",
            stage="received",
            filename="old.txt",
            source_uri=f"gs://bioaf-ingest-test/uploads/{upload_id}/old.txt",
            reference="UPL-0LD000",
        )
    )
    await session.commit()
    adapter = _adapter()
    enrich, begin = _no_side_effects()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter), enrich, begin:
        file = await UploadService.retry_upload(session, admin_user.organization_id, upload_id)
        filed_uuid = file.uuid
        await session.commit()
    session.expire_all()
    cp = await session.get(UploadCheckpoint, upload_id)
    assert cp.file_uuid is not None
    assert filed_uuid == cp.file_uuid
