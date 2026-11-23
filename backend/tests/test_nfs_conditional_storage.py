"""Create-only copies and guarded deletes on NFS, with protected managed copies (12B.2b).

A verified NFS file's canonical bytes are a read-only managed artifact. Moving such a file copies
the artifact create-only to the file's organized place without touching it, and the artifact is
then removed only through its own managed-artifact procedure, only while it is the version that
was copied. The managed tree is never a create-only destination. Verification of the moved file is
re-established at its new object rather than carried over.
"""

from __future__ import annotations

import io
import stat
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.models import DestinationExists, ObjectChanged, ObjectIdentity
from app.adapters.storage.nfs import NfsStorageProvider
from app.exceptions import ValidationError
from app.models.file import File
from app.models.file_verification import FileVerification
from app.services import file_verification_service as fvs
from app.services import storage_cleanup
from app.services.file_organization import FileOrganizationService

pytestmark = pytest.mark.asyncio

PAYLOAD = b"@r1\nACGT\n+\nIIII\n" * 512
FILE_UUID = uuid.UUID(int=7)


@pytest.fixture
def nfs(tmp_path) -> NfsStorageProvider:
    return NfsStorageProvider(root=str(tmp_path))


async def _artifact(nfs, source="file://raw/runs/1/out/a.fastq.gz"):
    await nfs.write_bytes(source, PAYLOAD)
    return await nfs.prepare_managed_copy(source, dest_store="raw", file_uuid=FILE_UUID, artifact_uuid=uuid.uuid4())


async def test_a_managed_artifact_is_copied_without_being_touched(nfs):
    artifact = await _artifact(nfs)
    path = Path(nfs._fs.path(artifact.uri))
    before = (path.stat().st_ino, path.stat().st_mtime_ns, stat.S_IMODE(path.stat().st_mode))
    pinned = await nfs.object_identity(artifact.uri)
    dest = f"file://raw/experiments/1/files/{FILE_UUID}/a.fastq.gz"

    published = await nfs.copy_create_only(
        artifact.uri, dest, source_identity=pinned, metadata={"bioaf-file-uuid": str(FILE_UUID)}
    )

    assert await nfs.read_bytes(dest) == PAYLOAD and published.size_bytes == len(PAYLOAD)
    assert (path.stat().st_ino, path.stat().st_mtime_ns, stat.S_IMODE(path.stat().st_mode)) == before
    assert Path(nfs._fs.path(dest)).stat().st_ino != before[0]  # a new inode, never a link to the artifact
    with pytest.raises(DestinationExists):
        await nfs.copy_create_only(artifact.uri, dest, source_identity=pinned)


async def test_the_managed_tree_is_never_a_create_only_destination(nfs):
    await nfs.write_bytes("file://raw/a.txt", b"x")
    with pytest.raises(ValidationError):
        await nfs.copy_create_only("file://raw/a.txt", f"file://raw/verified-files/{FILE_UUID}/{uuid.uuid4()}/payload")


async def test_a_managed_artifact_is_removed_only_through_its_procedure_and_only_unchanged(nfs):
    artifact = await _artifact(nfs)
    observed = await nfs.object_identity(artifact.uri)
    with pytest.raises(ObjectChanged):
        await nfs.delete_if_unchanged(ObjectIdentity(uri=artifact.uri, size_bytes=1, version="local:0:0:0:1"))
    assert await nfs.exists(artifact.uri)
    assert await nfs.delete_if_unchanged(observed) is True
    assert not Path(nfs._fs.path(artifact.uri)).parent.exists()  # the whole artifact directory
    assert await nfs.delete_if_unchanged(observed) is False


async def test_the_ordinary_tree_uses_the_filesystem_exclusion(nfs):
    uri = "file://raw/uploads/1/a.txt"
    await nfs.upload_file(uri, io.BytesIO(b"v1"))
    observed = await nfs.object_identity(uri)
    await nfs.upload_file(uri, io.BytesIO(b"v2 longer"))
    with pytest.raises(ObjectChanged):
        await nfs.delete_if_unchanged(observed)
    assert await nfs.read_bytes(uri) == b"v2 longer"


@pytest_asyncio.fixture
async def verified_nfs_file(session, admin_user, nfs):
    from app.models.experiment import Experiment

    artifact = await _artifact(nfs)
    exp = Experiment(name="E", organization_id=admin_user.organization_id, owner_user_id=admin_user.id)
    session.add(exp)
    file = File(
        organization_id=admin_user.organization_id,
        uuid=FILE_UUID,
        storage_uri=artifact.uri,
        filename="a.fastq.gz",
        file_type="fastq",
        size_bytes=len(PAYLOAD),
    )
    session.add(file)
    await session.flush()
    session.add(
        FileVerification(
            organization_id=admin_user.organization_id,
            file_id=file.id,
            status=fvs.VERIFIED,
            expected_algorithm="md5",
            expected_checksum="0" * 32,
            expectation_resolved=True,
            observed_uri=artifact.uri,
            artifact_uuid=artifact.artifact_uuid,
            artifact_uri=artifact.uri,
        )
    )
    await session.commit()
    return file.id, exp.id, artifact


async def test_moving_a_verified_nfs_file_copies_its_artifact_and_reverifies(
    session, admin_user, nfs, verified_nfs_file
):
    file_id, exp_id, artifact = verified_nfs_file
    with patch("app.adapters.registry.get_storage_adapter", return_value=nfs):
        await FileOrganizationService.assign_file_to_experiment(
            session, file_id, exp_id, admin_user.id, org_id=admin_user.organization_id
        )
    file = await session.get(File, file_id, populate_existing=True)
    assert file.storage_uri == f"file://raw/experiments/{exp_id}/files/{FILE_UUID}/a.fastq.gz"
    assert await nfs.read_bytes(file.storage_uri) == PAYLOAD
    assert not await nfs.exists(artifact.uri)  # removed after the commit, through the managed procedure
    assert file.pending_storage_cleanup is None
    record = (await session.execute(select(FileVerification).execution_options(populate_existing=True))).scalar_one()
    # The artifact proved the old object only: the new one is queued for its own protected copy.
    assert record.status == fvs.QUEUED and record.observed_uri is None
    assert record.expectation_revision == 2


async def test_an_nfs_cleanup_of_a_changed_artifact_is_refused(session, admin_user, nfs, verified_nfs_file):
    file_id, exp_id, artifact = verified_nfs_file
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=nfs),
        patch.object(storage_cleanup, "cleanup_after_commit", lambda *a, **k: _noop()),
    ):
        await FileOrganizationService.assign_file_to_experiment(
            session, file_id, exp_id, admin_user.id, org_id=admin_user.organization_id
        )
    file = await session.get(File, file_id, populate_existing=True)
    file.pending_storage_cleanup = {**file.pending_storage_cleanup, "version": "local:0:0:0:1"}
    await session.commit()
    with patch("app.adapters.registry.get_storage_adapter", return_value=nfs):
        assert await storage_cleanup.cleanup_after_commit(file_id, admin_user.organization_id) == "changed"
    assert await nfs.exists(artifact.uri)


async def _noop():
    return "none"
