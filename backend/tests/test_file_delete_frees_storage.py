"""Deleting a file frees its bytes, and the tombstone says so.

Issue #86. Soft delete (decision 5 of 2026-08-19) kept the catalogue entry and
its UUID forever, which is right, but it also left every byte sitting in the
bucket. A scientist who deletes a 40 GB BAM to reclaim space reclaimed nothing
and was still billed for it, so deletion did not do the one thing its name
promises.

The two facts stay separate, as ``File.storage_deleted`` always intended: the
record is retired AND the bytes are freed, and the tombstone records that the
object is gone. What changes is that a user-initiated delete now performs both
acts instead of only the first.

The refusals matter as much as the deletion. If the object store cannot be
reached, nothing is deleted at all: retiring the record while the bytes survive
is exactly the state this issue exists to end. And bytes another live catalogue
entry still points at are never touched, because that entry's owner did not ask
for anything to be deleted.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

DOOMED_URI = "gs://bucket/doomed.bam"


def _headers(user, role_name: str = "admin") -> dict:
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return {"Authorization": f"Bearer {token}"}


def _storage(**methods):
    """Patch the BAL storage adapter, which is the boundary the bytes live behind."""
    adapter = AsyncMock()
    for name, value in methods.items():
        getattr(adapter, name).return_value = value
    return patch("app.adapters.registry.get_storage_adapter", return_value=adapter), adapter


@pytest_asyncio.fixture
async def world(session):
    org = Organization(name="FreeBytesOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    admin = User(
        email="admin@freebytes.test",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(admin)
    await session.flush()
    exp = Experiment(name="E", organization_id=org.id, status="fastq_uploaded", owner_user_id=admin.id)
    session.add(exp)
    await session.flush()
    doomed = File(
        organization_id=org.id,
        experiment_id=exp.id,
        filename="doomed.bam",
        storage_uri=DOOMED_URI,
        file_type="bam",
        source_type="upload",
        size_bytes=42_000_000_000,
        uploader_user_id=admin.id,
    )
    session.add(doomed)
    await session.flush()
    await session.commit()
    return {"org": org, "admin": admin, "exp": exp, "doomed": doomed}


async def _delete(client, world):
    return await client.delete(f"/api/files/{world['doomed'].id}", headers=_headers(world["admin"]))


async def _row(session, file_id: int):
    return (
        await session.execute(
            text("SELECT storage_deleted, deleted_at FROM files WHERE id = :fid").bindparams(fid=file_id)
        )
    ).first()


class TestTheBytesAreActuallyFreed:
    @pytest.mark.asyncio
    async def test_the_object_is_deleted_from_storage(self, client, world):
        """The whole issue. Reclaiming the space is what deletion is for."""
        patcher, adapter = _storage(delete=None)
        with patcher:
            assert (await _delete(client, world)).status_code == 200

        adapter.delete.assert_awaited_once_with(DOOMED_URI)

    @pytest.mark.asyncio
    async def test_the_tombstone_says_the_bytes_are_gone(self, client, session, world):
        """The UUID survives forever, but it must not imply the object is still
        fetchable. ``storage_deleted`` is that acknowledgement."""
        patcher, _ = _storage(delete=None)
        with patcher:
            await _delete(client, world)

        row = await _row(session, world["doomed"].id)
        assert row.storage_deleted is True
        assert row.deleted_at is not None

    @pytest.mark.asyncio
    async def test_an_object_already_missing_from_the_bucket_still_deletes(self, client, session, world):
        """Adapter deletes are idempotent, so a record whose bytes vanished on
        their own is not a file the scientist is stuck with forever."""
        patcher, _ = _storage(delete=None)
        with patcher:
            assert (await _delete(client, world)).status_code == 200

        assert (await _row(session, world["doomed"].id)).storage_deleted is True


class TestNothingIsDeletedWhenTheBytesCannotBe:
    @pytest.mark.asyncio
    async def test_a_storage_failure_leaves_the_file_alone(self, client, session, world):
        """Retiring the record while the object survives is the bug itself. An
        unreachable bucket must fail loudly instead of quietly repeating it."""
        patcher, adapter = _storage()
        adapter.delete.side_effect = RuntimeError("bucket unreachable")
        with patcher:
            r = await _delete(client, world)

        assert r.status_code == 502
        row = await _row(session, world["doomed"].id)
        assert row.deleted_at is None
        assert row.storage_deleted is False

    @pytest.mark.asyncio
    async def test_the_file_is_still_listed_after_a_storage_failure(self, client, world):
        """Said the way a scientist checks it: the file is still there."""
        patcher, adapter = _storage()
        adapter.delete.side_effect = RuntimeError("bucket unreachable")
        with patcher:
            await _delete(client, world)

        r = await client.get(
            "/api/files", params={"experiment_id": world["exp"].id}, headers=_headers(world["admin"])
        )
        assert [f["id"] for f in r.json()["files"]] == [world["doomed"].id]


class TestBytesSomebodyElseStillNeeds:
    @pytest.mark.asyncio
    async def test_an_object_a_live_record_still_points_at_is_not_touched(self, client, session, world):
        """Two catalogue entries can share one object. Deleting one entry must
        not destroy the other entry's data behind its back."""
        twin = File(
            organization_id=world["org"].id,
            experiment_id=world["exp"].id,
            filename="doomed.bam",
            storage_uri=DOOMED_URI,
            file_type="bam",
            source_type="upload",
            size_bytes=42_000_000_000,
            uploader_user_id=world["admin"].id,
        )
        session.add(twin)
        await session.flush()
        await session.commit()

        patcher, adapter = _storage(delete=None)
        with patcher:
            assert (await _delete(client, world)).status_code == 200

        adapter.delete.assert_not_awaited()
        row = await _row(session, world["doomed"].id)
        assert row.deleted_at is not None
        assert row.storage_deleted is False

    @pytest.mark.asyncio
    async def test_an_already_freed_object_is_not_deleted_twice(self, client, session, world):
        """The stack teardown path marks records ``storage_deleted`` without
        retiring them. There are no bytes left to free."""
        await session.execute(
            text("UPDATE files SET storage_deleted = true WHERE id = :fid").bindparams(fid=world["doomed"].id)
        )
        await session.commit()

        patcher, adapter = _storage(delete=None)
        with patcher:
            assert (await _delete(client, world)).status_code == 200

        adapter.delete.assert_not_awaited()
        assert (await _row(session, world["doomed"].id)).deleted_at is not None


class TestTheAuditRecordsTheReclaim:
    @pytest.mark.asyncio
    async def test_the_delete_entry_records_the_space_freed(self, client, session, world):
        """An audited lab asks what was destroyed and how much of it."""
        patcher, _ = _storage(delete=None)
        with patcher:
            await _delete(client, world)

        details = (
            await session.execute(
                text(
                    "SELECT details_json FROM audit_log WHERE entity_type = 'file' AND entity_id = :fid "
                    "AND action = 'delete'"
                ).bindparams(fid=world["doomed"].id)
            )
        ).scalar()
        assert details["storage_freed"] is True
        assert details["size_bytes"] == 42_000_000_000
