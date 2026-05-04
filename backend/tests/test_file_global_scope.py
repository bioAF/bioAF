"""Tests for the explicit is_global flag.

The flag distinguishes deliberately global files (org-scoped, no
project/experiment/sample) from files that are merely unassociated.
Both look identical in FK columns, so the UI relies on this flag to
display "Global" vs "Unlinked".
"""

import pytest

from app.services.file_service import FileService


@pytest.mark.asyncio
async def test_create_file_record_persists_is_global(session, admin_user):
    org_id = admin_user.organization_id

    f = await FileService.create_file_record(
        session,
        org_id=org_id,
        user_id=admin_user.id,
        filename="protocol.pdf",
        gcs_uri="gs://b/protocol.pdf",
        size_bytes=1234,
        md5_checksum=None,
        file_type="pdf",
        is_global=True,
    )
    await session.commit()

    fetched = await FileService.get_file(session, f.id, org_id)
    assert fetched is not None
    assert fetched.is_global is True
    assert fetched.project_id is None
    assert fetched.experiment_id is None


@pytest.mark.asyncio
async def test_create_file_record_defaults_to_not_global(session, admin_user):
    org_id = admin_user.organization_id

    f = await FileService.create_file_record(
        session,
        org_id=org_id,
        user_id=admin_user.id,
        filename="orphan.txt",
        gcs_uri="gs://b/orphan.txt",
        size_bytes=10,
        md5_checksum=None,
        file_type="txt",
    )
    await session.commit()

    fetched = await FileService.get_file(session, f.id, org_id)
    assert fetched is not None
    assert fetched.is_global is False
