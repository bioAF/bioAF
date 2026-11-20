"""08A: upload notifications and Integration API project events follow the commit they describe.

A subscriber on its own session (the notification router, a webhook dispatcher) must find the state an
event announces. So nothing is announced before the owning transaction commits, a rolled-back or failed
transaction announces nothing (now or in a later transaction on the same session), and an idempotent
replay announces nothing again.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app import database as database_module
from app.adapters.storage.s3 import S3StorageProvider
from app.models.activity_feed import ActivityFeedEntry
from app.models.experiment import Experiment
from app.models.file import File
from app.models.notification import Notification
from app.models.project import Project
from app.services.event_bus import event_bus
from app.services.event_types import (
    ALL_EVENT_TYPES,
    DATA_UPLOADED,
    INTEGRATION_PROJECT_CREATED,
    INTEGRATION_PROJECT_UPDATED,
)
from app.services.notification_router import NotificationRouter
from app.services.upload_service import UploadService, _pending_uploads
from tests.support.committed_view import CommittedView

pytestmark = pytest.mark.asyncio

DATA = b"@r\nACGT\n+\nIIII\n" * 10


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import s3 as module

    monkeypatch.setattr(module, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = S3StorageProvider(org_slug="t")
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
    return adapter


async def _file_row(s, payload):
    return await s.get(File, payload["entity_id"])


async def _project_row(s, payload):
    return await s.get(Project, payload["data"]["project_id"])


@pytest_asyncio.fixture
async def uploads():
    view = CommittedView([DATA_UPLOADED], read=_file_row)
    yield view
    view.close()


@pytest_asyncio.fixture
async def projects():
    view = CommittedView([INTEGRATION_PROJECT_CREATED, INTEGRATION_PROJECT_UPDATED], read=_project_row)
    yield view
    view.close()


@pytest_asyncio.fixture
async def router():
    """The production notification router, on the test database, for the duration of one test."""
    r = NotificationRouter(database_module.async_session_factory)
    r.register()
    yield r
    for event_type in ALL_EVENT_TYPES:
        event_bus.unsubscribe(event_type, r._handle_event)


async def _experiment(session, admin_user) -> Experiment:
    project = Project(organization_id=admin_user.organization_id, name="P")
    session.add(project)
    await session.flush()
    exp = Experiment(organization_id=admin_user.organization_id, project_id=project.id, name="E", status="registered")
    session.add(exp)
    await session.commit()
    return exp


async def _pending(store, admin_user, *, experiment_id=None) -> str:
    upload_id = str(uuid.uuid4())
    uri = f"s3://bioaf-ingest-t/uploads/{upload_id}/notes.txt"
    await store.write_bytes(uri, DATA)
    _pending_uploads[upload_id] = {
        "org_id": admin_user.organization_id,
        "user_id": admin_user.id,
        "filename": "notes.txt",
        "gcs_uri": uri,
        "expected_size": len(DATA),
        "expected_md5": None,
        "project_id": None,
        "experiment_id": experiment_id,
        "sample_ids": [],
        "is_global": experiment_id is None,
        "external_id": None,
    }
    return upload_id


# --- DATA_UPLOADED ------------------------------------------------------------------------------


async def test_an_upload_is_announced_only_once_its_file_is_committed(session, admin_user, store, uploads):
    exp = await _experiment(session, admin_user)
    upload_id = await _pending(store, admin_user, experiment_id=exp.id)

    f = await UploadService.complete_upload(session, admin_user.organization_id, upload_id, "")
    await uploads.settle()
    assert uploads.events == []

    await session.commit()
    await uploads.settle()

    [(payload, row)] = uploads.seen(DATA_UPLOADED)
    assert row is not None and row.id == f.id and row.organization_id == admin_user.organization_id
    assert payload["org_id"] == admin_user.organization_id
    assert payload["user_id"] == admin_user.id
    assert (payload["entity_type"], payload["entity_id"]) == ("file", f.id)
    assert payload["metadata"] == {"experiment_id": exp.id}


async def test_a_rolled_back_upload_is_never_announced(session, admin_user, store, uploads):
    upload_id = await _pending(store, admin_user)

    await UploadService.complete_upload(session, admin_user.organization_id, upload_id, "")
    await uploads.settle()
    await session.rollback()

    # A later, unrelated transaction on the same session must not publish it either.
    await session.execute(text("SELECT 1"))
    await session.commit()
    await uploads.settle()

    assert uploads.events == []
    assert (await session.execute(select(File).where(File.filename == "notes.txt"))).first() is None


async def test_the_upload_notification_reaches_the_bell_and_names_the_committed_file(
    client, admin_token, session, admin_user, store, router
):
    """The representative subscriber outcome: the activity feed row and the admin's in-app
    notification point at the file the upload committed, in the uploader's organization."""
    exp = await _experiment(session, admin_user)
    upload_id = await _pending(store, admin_user, experiment_id=exp.id)

    resp = await client.post(
        "/api/files/upload/complete",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"upload_id": upload_id},
    )
    assert resp.status_code == 200, resp.text
    file_id = resp.json()["id"]

    from app.services.post_commit_events import wait_for_deferred_events

    await wait_for_deferred_events()
    async with database_module.async_session_factory() as s:
        feed = (
            (await s.execute(select(ActivityFeedEntry).where(ActivityFeedEntry.event_type == DATA_UPLOADED)))
            .scalars()
            .all()
        )
        notes = (await s.execute(select(Notification).where(Notification.event_type == DATA_UPLOADED))).scalars().all()
        committed = await s.get(File, file_id)

    assert committed is not None
    assert [(e.organization_id, e.entity_type, e.entity_id) for e in feed] == [
        (admin_user.organization_id, "file", file_id)
    ]
    assert [(n.organization_id, n.user_id) for n in notes] == [(admin_user.organization_id, admin_user.id)]
    assert notes[0].metadata_json["entity_id"] == file_id
    assert notes[0].metadata_json["experiment_id"] == exp.id


# --- Integration API projects -------------------------------------------------------------------


async def test_a_created_project_is_announced_once_it_is_committed(client, integration_api_key, projects):
    resp = await client.post(
        "/api/v1/integrations/projects",
        headers=integration_api_key["headers"],
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    assert resp.status_code == 201
    await projects.settle()

    [(payload, row)] = projects.seen(INTEGRATION_PROJECT_CREATED)
    assert row is not None and row.id == resp.json()["id"]
    assert payload["organization_id"] == row.organization_id
    assert payload["data"] == {"project_id": row.id, "external_id": "LIMS-A", "name": "Atlas"}


async def test_a_create_that_fails_before_commit_announces_nothing(
    client, integration_api_key, session, projects, monkeypatch
):
    """The idempotency record is written after the project; if it fails, the project rolls back and
    nothing may announce it."""
    from app.services import idempotency_service

    async def broken_record(*args, **kwargs):
        raise RuntimeError("idempotency store unavailable")

    monkeypatch.setattr(idempotency_service, "record", broken_record)
    headers = {**integration_api_key["headers"], "Idempotency-Key": "k-fail"}

    with pytest.raises(RuntimeError):
        await client.post("/api/v1/integrations/projects", headers=headers, json={"name": "A", "external_id": "X"})
    await projects.settle()

    assert projects.events == []
    assert (await session.execute(text("SELECT count(*) FROM projects"))).scalar() == 0


async def test_an_idempotent_replay_announces_nothing_more(client, integration_api_key, projects):
    headers = {**integration_api_key["headers"], "Idempotency-Key": "k-1"}
    body = {"name": "Atlas", "external_id": "LIMS-A"}

    first = await client.post("/api/v1/integrations/projects", headers=headers, json=body)
    second = await client.post("/api/v1/integrations/projects", headers=headers, json=body)
    assert second.headers.get("Idempotency-Replayed") == "true"
    await projects.settle()

    assert [p["data"]["project_id"] for p in projects.payloads(INTEGRATION_PROJECT_CREATED)] == [first.json()["id"]]


async def test_a_project_update_is_announced_with_the_committed_values(client, integration_api_key, projects):
    created = await client.post(
        "/api/v1/integrations/projects",
        headers=integration_api_key["headers"],
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    project_id = created.json()["id"]

    resp = await client.patch(
        f"/api/v1/integrations/projects/{project_id}",
        headers=integration_api_key["headers"],
        json={"name": "Atlas 2"},
    )
    assert resp.status_code == 200
    await projects.settle()

    [(payload, row)] = projects.seen(INTEGRATION_PROJECT_UPDATED)
    assert row.name == "Atlas 2"
    assert payload["data"] == {"project_id": project_id, "external_id": "LIMS-A", "changed_fields": ["name"]}
