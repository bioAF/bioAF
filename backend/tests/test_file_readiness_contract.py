"""01B.1 readiness contract: FileService.finalize_registration and FileService.mark_ready.

``file.registered`` is deferred once for a new File with its completed initial links. ``file.ready``
is deferred once, when ``ready_at`` first moves from NULL in a committed transaction, after the
final object was observed and any required checksum comparison succeeded.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.models import ObjectMetadata, StorageObjectNotFound
from app.models.experiment import Experiment
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.project import Project
from app.models.sample import Sample
from app.services.event_types import INTEGRATION_FILE_READY, INTEGRATION_FILE_REGISTERED
from app.services.file_service import FileService, ReadinessRefused
from tests.support.event_capture import EventCapture

pytestmark = pytest.mark.asyncio


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, ObjectMetadata | BaseException] = {}
        self.calls: list[str] = []

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        self.calls.append(uri)
        found = self.objects.get(uri)
        if found is None:
            raise StorageObjectNotFound(uri)
        if isinstance(found, BaseException):
            raise found
        return found


@pytest.fixture
def storage(monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: fake)
    return fake


@pytest_asyncio.fixture
async def events():
    capture = EventCapture([INTEGRATION_FILE_REGISTERED, INTEGRATION_FILE_READY])
    yield capture
    capture.close()


async def _linked_file(session, org_id, *, uri="s3://raw/a.fastq.gz", size=100, external_id="LIMS-F-1") -> File:
    project = Project(organization_id=org_id, name="P", external_id="PRJ-1")
    session.add(project)
    await session.flush()
    exp = Experiment(organization_id=org_id, project_id=project.id, name="E", external_id="EXP-1")
    session.add(exp)
    await session.flush()
    sample = Sample(experiment_id=exp.id, external_id="S-1")
    session.add(sample)
    await session.flush()
    f = await FileService.create_file_record(
        session,
        org_id=org_id,
        user_id=None,
        filename="a.fastq.gz",
        storage_uri=uri,
        size_bytes=size,
        md5_checksum=None,
        file_type="fastq",
        experiment_id=exp.id,
        external_id=external_id,
    )
    await FileService.link_file_to_sample(session, f.id, sample.id)
    return f


# --- registration -----------------------------------------------------------------------------


async def test_registration_snapshot_is_deferred_once_with_completed_links(session, admin_user, events):
    f = await _linked_file(session, admin_user.organization_id)
    await FileService.finalize_registration(session, f)
    await FileService.finalize_registration(session, f)  # idempotent within the transaction
    await events.settle()
    assert events.of(INTEGRATION_FILE_REGISTERED) == []  # nothing before commit

    await session.commit()
    await events.settle()
    [payload] = events.of(INTEGRATION_FILE_REGISTERED)
    sample_id = payload["data"]["sample_ids"][0]
    project_id = (await session.get(Experiment, f.experiment_id)).project_id
    assert project_id is not None
    assert payload == {
        "organization_id": admin_user.organization_id,
        "data": {
            "file_id": f.id,
            "external_id": "LIMS-F-1",
            "filename": "a.fastq.gz",
            "file_type": "fastq",
            "source_type": "upload",
            "project_id": project_id,
            "project_external_id": "PRJ-1",
            "experiment_id": f.experiment_id,
            "experiment_external_id": "EXP-1",
            "sample_ids": [sample_id],
            "samples": [
                {
                    "sample_id": sample_id,
                    "external_id": "S-1",
                    "experiment_id": f.experiment_id,
                    "experiment_external_id": "EXP-1",
                }
            ],
        },
    }


async def test_a_snapshot_does_not_follow_later_relabeling(session, admin_user, events):
    f = await _linked_file(session, admin_user.organization_id)
    await FileService.finalize_registration(session, f)
    exp = await session.get(Experiment, f.experiment_id)
    exp.external_id = "RELABELED"
    f.external_id = "CHANGED"
    await session.commit()
    await events.settle()
    [payload] = events.of(INTEGRATION_FILE_REGISTERED)
    assert payload["data"]["external_id"] == "LIMS-F-1"
    assert payload["data"]["experiment_external_id"] == "EXP-1"


async def test_rolled_back_registration_publishes_nothing(session, admin_user, events):
    f = await _linked_file(session, admin_user.organization_id)
    await FileService.finalize_registration(session, f)
    await session.rollback()
    await events.settle()
    assert events.of(INTEGRATION_FILE_REGISTERED) == []


async def test_registration_in_a_rolled_back_savepoint_publishes_nothing(session, admin_user, events):
    async with session.begin_nested() as sp:
        f = await _linked_file(session, admin_user.organization_id)
        await FileService.finalize_registration(session, f)
        await sp.rollback()
    await session.commit()
    await events.settle()
    assert events.of(INTEGRATION_FILE_REGISTERED) == []


# --- readiness --------------------------------------------------------------------------------


async def test_checksum_optional_readiness_uses_observed_size(session, admin_user, storage, events):
    f = await _linked_file(session, admin_user.organization_id, size=None)
    storage.objects[f.storage_uri] = ObjectMetadata(uri=f.storage_uri, size_bytes=4096, md5_hash="ab" * 16)

    outcome = await FileService.mark_ready(session, f.id, admin_user.organization_id)
    assert outcome == "ready"
    await session.commit()
    await events.settle()

    fresh = await session.get(File, f.id)
    await session.refresh(fresh)
    assert fresh.ready_at is not None and fresh.size_bytes == 4096
    # Provider metadata is not a supplied expectation: no comparison, no proof recorded.
    assert fresh.md5_checksum is None
    [payload] = events.of(INTEGRATION_FILE_READY)
    assert payload["data"]["file_id"] == f.id
    assert payload["data"]["size_bytes"] == 4096
    assert payload["data"]["external_id"] == "LIMS-F-1"
    assert payload["data"]["ready_at"].endswith("Z")
    assert set(payload["data"]) == {
        "file_id",
        "external_id",
        "ready_at",
        "size_bytes",
        "project_id",
        "project_external_id",
        "experiment_id",
        "experiment_external_id",
        "sample_ids",
        "samples",
    }


async def test_ready_is_a_single_transition(session, admin_user, storage, events):
    f = await _linked_file(session, admin_user.organization_id)
    storage.objects[f.storage_uri] = ObjectMetadata(uri=f.storage_uri, size_bytes=100)
    assert await FileService.mark_ready(session, f.id, admin_user.organization_id) == "ready"
    await session.commit()
    assert await FileService.mark_ready(session, f.id, admin_user.organization_id) == "already_ready"
    await session.commit()
    await events.settle()
    assert len(events.of(INTEGRATION_FILE_READY)) == 1


async def test_rollback_leaves_the_file_unready_and_silent(session, admin_user, storage, events):
    f = await _linked_file(session, admin_user.organization_id)
    file_id, uri, org = f.id, f.storage_uri, admin_user.organization_id
    await session.commit()
    storage.objects[uri] = ObjectMetadata(uri=uri, size_bytes=100)
    await FileService.mark_ready(session, file_id, org)
    await session.rollback()
    await events.settle()
    assert events.of(INTEGRATION_FILE_READY) == []
    assert (await session.get(File, file_id)).ready_at is None


@pytest.mark.parametrize(
    ("observed", "expected_size", "code"),
    [
        (None, None, "object_missing"),
        (ObjectMetadata(uri="x", size_bytes=None), None, "size_unavailable"),
        (ObjectMetadata(uri="x", size_bytes=-1), None, "size_unavailable"),
        (ObjectMetadata(uri="x", size_bytes=99), 100, "size_mismatch"),
        (ConnectionError("down"), None, "storage_unavailable"),
    ],
)
async def test_failed_observation_is_visible_and_not_ready(
    session, admin_user, storage, events, observed, expected_size, code
):
    f = await _linked_file(session, admin_user.organization_id)
    if observed is not None:
        storage.objects[f.storage_uri] = observed

    outcome = await FileService.mark_ready(session, f.id, admin_user.organization_id, expected_size=expected_size)
    await session.commit()
    await events.settle()

    assert outcome == "failed"
    assert (await session.get(File, f.id)).ready_at is None
    assert events.of(INTEGRATION_FILE_READY) == []
    record = (await session.execute(select(FileVerification).where(FileVerification.file_id == f.id))).scalar_one()
    assert record.status == "failed" and record.error_code == code


@pytest.mark.parametrize("status", ["waiting_expectation", "queued", "preparing", "verifying", "checksum_mismatch"])
async def test_a_required_check_blocks_readiness(session, admin_user, storage, events, status):
    f = await _linked_file(session, admin_user.organization_id)
    storage.objects[f.storage_uri] = ObjectMetadata(uri=f.storage_uri, size_bytes=100)
    session.add(
        FileVerification(
            organization_id=admin_user.organization_id,
            file_id=f.id,
            status=status,
            expectation_source="upload_assertion",
            expected_algorithm="md5",
            expected_checksum="a" * 32,
        )
    )
    await session.flush()
    assert await FileService.mark_ready(session, f.id, admin_user.organization_id) == "pending"
    await session.commit()
    await events.settle()
    assert events.of(INTEGRATION_FILE_READY) == []


async def test_a_verified_result_for_the_current_object_allows_readiness(session, admin_user, storage, events):
    f = await _linked_file(session, admin_user.organization_id)
    storage.objects[f.storage_uri] = ObjectMetadata(uri=f.storage_uri, size_bytes=100)
    record = FileVerification(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        status="verified",
        expectation_source="upload_assertion",
        expected_algorithm="md5",
        expected_checksum="a" * 32,
        observed_checksum="a" * 32,
        observed_method="streamed",
        observed_bytes=100,
        observed_uri="s3://raw/elsewhere",  # stale: proof for another object
    )
    session.add(record)
    await session.flush()
    assert await FileService.mark_ready(session, f.id, admin_user.organization_id) == "pending"

    record.observed_uri = f.storage_uri
    await session.flush()
    assert await FileService.mark_ready(session, f.id, admin_user.organization_id) == "ready"
    await session.commit()
    await events.settle()
    assert len(events.of(INTEGRATION_FILE_READY)) == 1
    # Independent observation populates the file's checksum.
    assert (await session.get(File, f.id)).md5_checksum == "a" * 32


@pytest.mark.parametrize("flag", ["deleted_at", "storage_deleted"])
async def test_deleted_files_are_refused(session, admin_user, storage, flag):
    f = await _linked_file(session, admin_user.organization_id)
    storage.objects[f.storage_uri] = ObjectMetadata(uri=f.storage_uri, size_bytes=100)
    if flag == "deleted_at":
        f.deleted_at = f.created_at
    else:
        f.storage_deleted = True
    await session.flush()
    with pytest.raises(ReadinessRefused):
        await FileService.mark_ready(session, f.id, admin_user.organization_id)
    assert storage.calls == []


async def test_another_organization_cannot_mark_a_file_ready(session, admin_user, storage):
    from app.models.organization import Organization

    f = await _linked_file(session, admin_user.organization_id)
    other = Organization(name="Other")
    session.add(other)
    await session.flush()
    with pytest.raises(ReadinessRefused):
        await FileService.mark_ready(session, f.id, other.id)


async def test_concurrent_finalization_produces_one_transition(db_engine, session, admin_user, storage, events):
    from app import database as database_module

    f = await _linked_file(session, admin_user.organization_id)
    storage.objects[f.storage_uri] = ObjectMetadata(uri=f.storage_uri, size_bytes=100)
    await session.commit()
    org = admin_user.organization_id

    async def finalize():
        async with database_module.async_session_factory() as s:
            outcome = await FileService.mark_ready(s, f.id, org)
            await asyncio.sleep(0.05)
            await s.commit()
            return outcome

    outcomes = await asyncio.gather(finalize(), finalize())
    await events.settle()
    assert sorted(outcomes) == ["already_ready", "ready"]
    assert len(events.of(INTEGRATION_FILE_READY)) == 1
