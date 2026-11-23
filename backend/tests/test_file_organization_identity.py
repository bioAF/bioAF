"""Organizing existing files keeps every file's bytes its own (12B.1e).

Assignment, reassignment and unlinking copy a File create-only to its UUID-keyed place, record the
old object for removal after the commit, and check the File and target against the caller's
organization before any storage call. A source other live records share is refused, an occupied
destination is reused only when it is provably this File's earlier copy, and auto-ingest (still
disabled in production) uses the same layout and keeps its source until registration commits.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.file import File
from app.services import storage_cleanup
from app.services.file_organization import FileOrganizationService
from tests.support.object_store import recording_store

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(session, admin_user):
    from app.models.component import PlatformConfig
    from app.models.experiment import Experiment
    from app.models.organization import Organization

    org = admin_user.organization_id
    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test"))
    session.add(PlatformConfig(key="raw_bucket_name", value="bioaf-raw-test"))
    exps = [Experiment(name=f"E{n}", organization_id=org, owner_user_id=admin_user.id) for n in range(2)]
    foreign = Organization(name="Foreign", setup_complete=True)
    session.add_all([*exps, foreign])
    await session.flush()
    theirs = Experiment(name="Theirs", organization_id=foreign.id)
    session.add(theirs)
    await session.commit()
    store = recording_store()
    with patch("app.adapters.registry.get_storage_adapter", return_value=store):
        yield {
            "org": org,
            "user": admin_user.id,
            "foreign": foreign.id,
            "exps": [e.id for e in exps],
            "theirs": theirs.id,
            "store": store,
        }


async def _file(session, org, uri, data, *, store, experiment_id=None, name="same.txt") -> int:
    store.put(uri, data)
    file = File(organization_id=org, storage_uri=uri, filename=name, file_type="other", experiment_id=experiment_id)
    session.add(file)
    await session.commit()
    return file.id


async def _row(session, file_id) -> File:
    return await session.get(File, file_id, populate_existing=True)


async def test_same_name_files_assigned_into_one_experiment_keep_their_own_bytes(session, world):
    store, exp = world["store"], world["exps"][0]
    a = await _file(session, world["org"], "gs://bioaf-ingest-test/uploads/1/same.txt", b"first", store=store)
    b = await _file(session, world["org"], "gs://bioaf-ingest-test/uploads/2/same.txt", b"second", store=store)
    for fid in (a, b):
        await FileOrganizationService.assign_file_to_experiment(session, fid, exp, world["user"], org_id=world["org"])
    fa, fb = await _row(session, a), await _row(session, b)
    assert fa.storage_uri == f"gs://bioaf-raw-test/experiments/{exp}/files/{fa.uuid}/same.txt"
    assert fa.storage_uri != fb.storage_uri
    assert (store.objects[fa.storage_uri].data, store.objects[fb.storage_uri].data) == (b"first", b"second")
    assert store.objects[fa.storage_uri].metadata == {
        "bioaf-file-uuid": str(fa.uuid),
        "bioaf-organization-id": str(world["org"]),
    }
    # The intake objects were removed after each move committed.
    assert [u for u in store.objects if "/uploads/" in u] == []
    assert fa.pending_storage_cleanup is None and fa.experiment_id == exp


async def test_unlinking_same_name_files_from_different_experiments_keeps_both(session, world):
    store, (e0, e1) = world["store"], world["exps"]
    a = await _file(
        session, world["org"], f"gs://bioaf-raw-test/experiments/{e0}/same.txt", b"A", store=store, experiment_id=e0
    )
    b = await _file(
        session, world["org"], f"gs://bioaf-raw-test/experiments/{e1}/same.txt", b"B", store=store, experiment_id=e1
    )
    for fid in (a, b):
        await FileOrganizationService.unlink_file_from_experiment(session, fid, world["user"], org_id=world["org"])
    fa, fb = await _row(session, a), await _row(session, b)
    assert fa.storage_uri == f"gs://bioaf-raw-test/unlinked/files/{fa.uuid}/same.txt"
    assert (store.objects[fa.storage_uri].data, store.objects[fb.storage_uri].data) == (b"A", b"B")
    assert fa.experiment_id is None and fb.experiment_id is None

    # Relinking keeps the same identity and bytes.
    await FileOrganizationService.assign_file_to_experiment(session, a, e1, world["user"], org_id=world["org"])
    fa = await _row(session, a)
    assert fa.storage_uri == f"gs://bioaf-raw-test/experiments/{e1}/files/{fa.uuid}/same.txt"
    assert store.objects[fa.storage_uri].data == b"A"


async def test_reassignment_records_its_audit_and_moves_once(session, world):
    store, (e0, e1) = world["store"], world["exps"]
    fid = await _file(session, world["org"], "gs://bioaf-ingest-test/uploads/3/a.txt", b"x", store=store)
    await FileOrganizationService.assign_file_to_experiment(session, fid, e0, world["user"], org_id=world["org"])
    await FileOrganizationService.reassign_file_to_experiment(session, fid, e1, world["user"], org_id=world["org"])
    actions = (
        (await session.execute(text("SELECT action FROM audit_log WHERE entity_type='file' ORDER BY id")))
        .scalars()
        .all()
    )
    assert actions[-2:] == ["assigned_to_experiment", "reassigned_to_experiment"]
    f = await _row(session, fid)
    assert list(store.objects) == [f.storage_uri]


async def test_a_source_other_records_share_is_refused_without_any_storage_call(session, world):
    from app.exceptions import ConflictError

    store = world["store"]
    uri = f"gs://bioaf-raw-test/experiments/{world['exps'][0]}/same.txt"
    a = await _file(session, world["org"], uri, b"latest bytes", store=store)
    session.add(File(organization_id=world["org"], storage_uri=uri, filename="same.txt", file_type="other"))
    await session.commit()
    before = len(store.calls)
    with pytest.raises(ConflictError):
        await FileOrganizationService.assign_file_to_experiment(
            session, a, world["exps"][1], world["user"], org_id=world["org"]
        )
    await session.rollback()
    assert store.calls[before:] == []
    f = await _row(session, a)
    assert (f.storage_uri, f.experiment_id) == (uri, None)


@pytest.mark.parametrize("who", ["foreign-file", "foreign-target", "deleted-file"])
async def test_another_organizations_file_or_target_is_not_found_and_nothing_moves(session, world, who):
    from datetime import datetime, timezone

    from app.exceptions import NotFoundError

    store = world["store"]
    owner = world["foreign"] if who == "foreign-file" else world["org"]
    fid = await _file(session, owner, "gs://bioaf-ingest-test/uploads/4/a.txt", b"x", store=store)
    if who == "deleted-file":
        (await _row(session, fid)).deleted_at = datetime.now(timezone.utc)
        await session.commit()
    target = world["theirs"] if who == "foreign-target" else world["exps"][0]
    before = len(store.calls)
    with pytest.raises(NotFoundError):
        await FileOrganizationService.assign_file_to_experiment(
            session, fid, target, world["user"], org_id=world["org"]
        )
    await session.rollback()
    assert store.calls[before:] == []
    assert (await _row(session, fid)).storage_uri == "gs://bioaf-ingest-test/uploads/4/a.txt"
    audit = (await session.execute(text("SELECT count(*) FROM audit_log WHERE action LIKE '%experiment%'"))).scalar()
    assert audit == 0


async def test_the_v1_endpoints_refuse_another_organizations_file(client, admin_token, session, world):
    store = world["store"]
    fid = await _file(session, world["foreign"], "gs://bioaf-ingest-test/uploads/5/a.txt", b"x", store=store)
    auth = {"Authorization": f"Bearer {admin_token}"}
    before = len(store.calls)
    assign = await client.post(f"/api/v1/files/{fid}/assign", json={"experiment_id": world["exps"][0]}, headers=auth)
    unlink = await client.post(f"/api/v1/files/{fid}/unlink", headers=auth)
    assert (assign.status_code, unlink.status_code) == (404, 404)
    assert "Foreign" not in assign.text + unlink.text
    assert store.calls[before:] == []


async def test_an_interrupted_move_to_the_same_target_is_reused(session, world):
    store, exp = world["store"], world["exps"][0]
    fid = await _file(session, world["org"], "gs://bioaf-ingest-test/uploads/6/a.txt", b"x", store=store)
    f = await _row(session, fid)
    dest = f"gs://bioaf-raw-test/experiments/{exp}/files/{f.uuid}/same.txt"
    store.put(dest, b"x", {"bioaf-file-uuid": str(f.uuid), "bioaf-organization-id": str(world["org"])})
    await FileOrganizationService.assign_file_to_experiment(session, fid, exp, world["user"], org_id=world["org"])
    assert (await _row(session, fid)).storage_uri == dest
    store.copy_create_only.assert_not_called()


async def test_an_unrelated_object_at_the_destination_refuses_the_move(session, world):
    from app.exceptions import ConflictError

    store, exp = world["store"], world["exps"][0]
    fid = await _file(session, world["org"], "gs://bioaf-ingest-test/uploads/7/a.txt", b"x", store=store)
    f = await _row(session, fid)
    dest = f"gs://bioaf-raw-test/experiments/{exp}/files/{f.uuid}/same.txt"
    store.put(dest, b"x")  # same size, no tags
    with pytest.raises(ConflictError):
        await FileOrganizationService.assign_file_to_experiment(session, fid, exp, world["user"], org_id=world["org"])
    await session.rollback()
    f = await _row(session, fid)
    assert (f.storage_uri, f.experiment_id) == ("gs://bioaf-ingest-test/uploads/7/a.txt", None)
    assert store.objects[dest].metadata == {} and "gs://bioaf-ingest-test/uploads/7/a.txt" in store.objects


async def test_concurrent_changes_to_one_file_serialize(session, world):
    from app import database

    store, (e0, e1) = world["store"], world["exps"]
    fid = await _file(session, world["org"], "gs://bioaf-ingest-test/uploads/8/a.txt", b"x", store=store)

    async def move(target):
        async with database.async_session_factory() as s:
            if target is None:
                await FileOrganizationService.unlink_file_from_experiment(s, fid, world["user"], org_id=world["org"])
            else:
                await FileOrganizationService.assign_file_to_experiment(
                    s, fid, target, world["user"], org_id=world["org"]
                )

    await asyncio.gather(move(e0), move(e1), move(None))
    await storage_cleanup.reconcile_due(database.async_session_factory)
    f = await _row(session, fid)
    # One consistent outcome: the record names an object that holds the bytes, and nothing is left over.
    assert store.objects[f.storage_uri].data == b"x"
    assert list(store.objects) == [f.storage_uri]
    assert f.pending_storage_cleanup is None


async def test_moving_a_verified_file_keeps_its_verification_truthful(session, world):
    from app.models.file_verification import FileVerification
    from app.services import file_verification_service as fvs

    store, exp = world["store"], world["exps"][0]
    fid = await _file(session, world["org"], "gs://bioaf-ingest-test/uploads/9/a.txt", b"x", store=store)
    session.add(
        FileVerification(
            organization_id=world["org"],
            file_id=fid,
            status=fvs.VERIFIED,
            expected_checksum="0" * 32,
            expected_algorithm="md5",
            expectation_resolved=True,
            observed_uri="gs://bioaf-ingest-test/uploads/9/a.txt",
        )
    )
    await session.commit()
    await FileOrganizationService.assign_file_to_experiment(session, fid, exp, world["user"], org_id=world["org"])
    record = (await session.execute(select(FileVerification).execution_options(populate_existing=True))).scalar_one()
    assert record.status == fvs.QUEUED and record.observed_uri is None  # the in-memory store has no digest


# ---------------------------------------------------------------------------
# auto-ingest (production entry stays disabled)
# ---------------------------------------------------------------------------


async def _ingested(session, world, path):
    from app.services.ingest_service import organize_ingested_file

    file = File(
        organization_id=world["org"],
        storage_uri=f"gs://bioaf-ingest-test/{path}",
        filename=path.rsplit("/", 1)[-1],
        file_type="other",
        uuid=uuid.uuid4(),
    )
    session.add(file)
    await session.flush()
    await organize_ingested_file(
        session,
        file,
        source_bucket="bioaf-ingest-test",
        source_path=path,
        raw_bucket="bioaf-raw-test",
        filename=file.filename,
        experiment_id=None,
        cleanup_policy="delete_after_copy",
    )
    return file


async def test_auto_ingest_files_under_the_uuid_layout_and_keeps_its_source_until_commit(session, world):
    """The raw-copy step of the gated auto-ingest pipeline (its matcher no longer exists, so the
    pipeline itself cannot run; production entry stays disabled)."""
    from app.services.auto_ingest_gate import AUTO_INGEST_DISABLED

    assert AUTO_INGEST_DISABLED is True
    store = world["store"]
    store.put("gs://bioaf-ingest-test/drop/same.txt", b"ingested")
    file = await _ingested(session, world, "drop/same.txt")
    assert file.storage_uri == f"gs://bioaf-raw-test/unlinked/files/{file.uuid}/same.txt"
    assert "gs://bioaf-ingest-test/drop/same.txt" in store.objects  # kept until registration commits
    await session.commit()
    assert await storage_cleanup.cleanup_after_commit(file.id, world["org"]) == "deleted"
    assert "gs://bioaf-ingest-test/drop/same.txt" not in store.objects
    assert store.objects[file.storage_uri].data == b"ingested"


async def test_a_failed_auto_ingest_registration_keeps_its_source(session, world):
    store = world["store"]
    store.put("gs://bioaf-ingest-test/drop/b.txt", b"ingested")
    await _ingested(session, world, "drop/b.txt")
    await session.rollback()  # registration never commits
    assert store.objects["gs://bioaf-ingest-test/drop/b.txt"].data == b"ingested"
    assert (await session.execute(select(File))).scalars().all() == []
