"""Direct uploads through provider sessions bioAF can cancel (11F/11G; review finding #2).

A cancellation is recorded at once and stops filing, but the upload keeps counting as a possible
writer until the provider confirms nothing more can arrive. Background reconciliation settles
cancellations, abandoned sessions, and completions nobody reported; a completion that raced a
cancellation is cleaned up. When the provider cannot confirm an outcome, the upload stays a
writer until an administrator releases it (audited, with an acknowledgement). Presigned URLs
issued before this release settle after their expiry plus one hour, then by the stored object.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.adapters.models import UploadSessionGrant, UploadSessionState, UploadSessionStatus
from app.models.file import File
from app.models.upload_checkpoint import UploadCheckpoint
from app.services import storage_write_admission
from app.services.upload_service import UploadService

GCS_SESSION = "https://storage.googleapis.com/upload?upload_id=SECRET-SESSION"


class FakeStore:
    """A storage adapter with scripted session answers."""

    named_containers = True

    def __init__(self, protocol="gcs_resumable"):
        self.protocol = protocol
        self.inspect_answers: list[UploadSessionStatus] = []
        self.cancel_answers: list[UploadSessionStatus] = []
        self.finished = None
        self.deleted: list[str] = []
        self.objects: set[str] = set()
        self.cancel_calls = 0
        self.inspect_calls = 0

    def build_uri(self, bucket, key):
        return f"gs://{bucket}/{key.lstrip('/')}"

    def parse_uri(self, uri):
        parsed = urlparse(uri)
        return parsed.netloc, parsed.path.lstrip("/")

    async def resolve_uri(self, store, key):
        return f"gs://bioaf-{store.value}-test/{key}"

    async def begin_upload_session(self, uri, *, content_type="application/octet-stream", size_bytes=None, origin=None):
        if self.protocol == "s3_multipart":
            return UploadSessionGrant(protocol="s3_multipart", handle="UPLOAD-1", part_size=8 * 1024 * 1024)
        return UploadSessionGrant(protocol="gcs_resumable", handle=GCS_SESSION, url=GCS_SESSION)

    async def sign_upload_parts(self, uri, handle, numbers):
        return {n: f"https://s3.example/part{n}" for n in numbers}

    async def finish_upload_session(self, uri, handle, parts):
        self.finished = parts
        self.objects.add(uri)

    async def inspect_upload_session(self, uri, handle):
        self.inspect_calls += 1
        return self.inspect_answers.pop(0) if self.inspect_answers else UploadSessionStatus(state="active")

    async def cancel_upload_session(self, uri, handle):
        self.cancel_calls += 1
        return self.cancel_answers.pop(0) if self.cancel_answers else UploadSessionStatus(state="ended")

    async def exists(self, uri):
        return uri in self.objects

    async def delete(self, uri, **_kw):
        self.deleted.append(uri)
        self.objects.discard(uri)

    async def move(self, src, dst):
        return dst

    async def inspect_container(self, uri):
        from app.adapters.models import ContainerObservation, ContainerPresence

        return ContainerObservation(presence=ContainerPresence.PRESENT, simulated=True)


def _status(state, received=None):
    return UploadSessionStatus(state=UploadSessionState(state), received_bytes=received)


@pytest_asyncio.fixture
async def storage(session, admin_user):
    from app.models.component import PlatformConfig

    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test"))
    session.add(PlatformConfig(key="raw_bucket_name", value="bioaf-raw-test"))
    await session.commit()


@pytest.fixture
def store():
    fake = FakeStore()
    with patch("app.adapters.registry.get_storage_adapter", return_value=fake):
        yield fake


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _initiate(client, token, **extra):
    body = {"filename": "a.fastq.gz", "expected_size_bytes": 4, **extra}
    response = await client.post("/api/files/upload/initiate", json=body, headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _checkpoint(session, upload_id) -> UploadCheckpoint:
    session.expire_all()
    return await session.get(UploadCheckpoint, upload_id)


async def _writers(session):
    return {w.upload_id for w in await storage_write_admission.outstanding_writers(session)}


async def _reconcile():
    from app import database
    from app.services.upload_settlement import reconcile_due

    return await reconcile_due(database.async_session_factory)


# ---------------------------------------------------------------------------
# issuing a session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_records_a_session_the_browser_uploads_to(client, admin_token, storage, store, session):
    body = await _initiate(client, admin_token)

    assert body["upload"] == {"protocol": "gcs_resumable", "url": GCS_SESSION, "part_size": None}
    assert body["signed_url"] == GCS_SESSION  # a whole-file PUT to a GCS session still works
    cp = await _checkpoint(session, body["upload_id"])
    assert (cp.kind, cp.stage, cp.session_protocol, cp.session_handle) == (
        "session",
        "issued",
        "gcs_resumable",
        GCS_SESSION,
    )
    assert await _writers(session) == {cp.id}


@pytest.mark.asyncio
async def test_an_s3_upload_gets_part_urls_only_for_its_uploader(
    client, admin_token, bench_token, storage, session
):
    fake = FakeStore("s3_multipart")
    with patch("app.adapters.registry.get_storage_adapter", return_value=fake):
        body = await _initiate(client, admin_token)
        assert body["upload"] == {"protocol": "s3_multipart", "url": None, "part_size": 8 * 1024 * 1024}
        assert body["signed_url"] is None
        upload_id = body["upload_id"]
        mine = await client.post(
            f"/api/files/uploads/{upload_id}/parts", json={"part_numbers": [1, 2]}, headers=_auth(admin_token)
        )
        theirs = await client.post(
            f"/api/files/uploads/{upload_id}/parts", json={"part_numbers": [1]}, headers=_auth(bench_token)
        )
    assert mine.status_code == 200
    assert mine.json() == {"urls": {"1": "https://s3.example/part1", "2": "https://s3.example/part2"}}
    assert theirs.status_code == 404


@pytest.mark.asyncio
async def test_completing_an_s3_upload_assembles_its_parts_then_files_it(client, admin_token, storage, session):
    fake = FakeStore("s3_multipart")
    fake.inspect_answers = [_status("completed")]
    with patch("app.adapters.registry.get_storage_adapter", return_value=fake):
        upload_id = (await _initiate(client, admin_token))["upload_id"]
        done = await client.post(
            "/api/files/upload/complete",
            json={"upload_id": upload_id, "parts": [{"number": 1, "etag": '"abc"'}]},
            headers=_auth(admin_token),
        )
    assert done.status_code == 200, done.text
    assert [p.number for p in fake.finished] == [1]
    assert (await _checkpoint(session, upload_id)).stage == "filed"
    assert await _writers(session) == set()


@pytest.mark.asyncio
async def test_completion_before_the_provider_has_the_object_is_refused(client, admin_token, storage, store, session):
    store.inspect_answers = [_status("active", 2)]
    upload_id = (await _initiate(client, admin_token))["upload_id"]
    done = await client.post("/api/files/upload/complete", json={"upload_id": upload_id}, headers=_auth(admin_token))
    assert done.status_code == 409
    assert done.json()["code"] == "upload_not_finished"
    assert (await _checkpoint(session, upload_id)).stage == "issued"


# ---------------------------------------------------------------------------
# cancellation is settled by the provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_confirmed_cancellation_settles_the_writer(client, admin_token, storage, store, session):
    upload_id = (await _initiate(client, admin_token))["upload_id"]
    store.cancel_answers = [_status("ended")]

    response = await client.post(f"/api/files/uploads/{upload_id}/cancel", headers=_auth(admin_token))

    assert response.status_code == 204
    cp = await _checkpoint(session, upload_id)
    assert cp.stage == "cancelled"
    assert cp.cancel_requested_at is not None
    assert await _writers(session) == set()
    audit = (await session.execute(text("SELECT count(*) FROM audit_log WHERE action='upload_cancelled'"))).scalar()
    assert audit == 1


@pytest.mark.asyncio
async def test_an_unconfirmed_cancellation_keeps_the_writer_and_refuses_filing(
    client, admin_token, storage, store, session
):
    upload_id = (await _initiate(client, admin_token))["upload_id"]
    store.cancel_answers = [_status("unknown")]

    await client.post(f"/api/files/uploads/{upload_id}/cancel", headers=_auth(admin_token))

    cp = await _checkpoint(session, upload_id)
    assert cp.stage == "cancelling"
    assert await _writers(session) == {upload_id}
    store.inspect_answers = [_status("completed")]
    done = await client.post("/api/files/upload/complete", json={"upload_id": upload_id}, headers=_auth(admin_token))
    assert done.status_code == 400
    assert (await session.execute(select(File))).scalars().all() == []


@pytest.mark.asyncio
async def test_background_reconciliation_confirms_a_pending_cancellation(client, admin_token, storage, store, session):
    upload_id = (await _initiate(client, admin_token))["upload_id"]
    store.cancel_answers = [_status("unknown"), _status("ended")]
    await client.post(f"/api/files/uploads/{upload_id}/cancel", headers=_auth(admin_token))
    await session.execute(text("UPDATE upload_checkpoints SET next_reconcile_at = now() - interval '1 second'"))
    await session.commit()

    await _reconcile()

    assert (await _checkpoint(session, upload_id)).stage == "cancelled"
    assert await _writers(session) == set()


@pytest.mark.asyncio
async def test_a_completion_that_raced_the_cancellation_is_cleaned_up(client, admin_token, storage, store, session):
    upload_id = (await _initiate(client, admin_token))["upload_id"]
    cp = await _checkpoint(session, upload_id)
    store.objects.add(cp.source_uri)
    store.cancel_answers = [_status("completed")]

    await client.post(f"/api/files/uploads/{upload_id}/cancel", headers=_auth(admin_token))

    cp = await _checkpoint(session, upload_id)
    assert cp.stage == "cancelled"
    assert store.deleted == [cp.source_uri]
    assert (await session.execute(select(File))).scalars().all() == []


# ---------------------------------------------------------------------------
# reconciliation without the browser
# ---------------------------------------------------------------------------


async def _issued(client, token, session):
    upload_id = (await _initiate(client, token))["upload_id"]
    await session.execute(text("UPDATE upload_checkpoints SET next_reconcile_at = now() - interval '1 second'"))
    await session.commit()
    return upload_id


@pytest.mark.asyncio
async def test_a_completed_upload_nobody_reported_is_filed(client, admin_token, storage, store, session):
    upload_id = await _issued(client, admin_token, session)
    store.inspect_answers = [_status("completed")]

    await _reconcile()

    cp = await _checkpoint(session, upload_id)
    assert cp.stage == "received"
    assert cp.next_attempt_at is not None  # the automatic filing retry picks it up
    assert await _writers(session) == set()


@pytest.mark.asyncio
async def test_an_expired_session_that_stored_nothing_settles(client, admin_token, storage, store, session):
    upload_id = await _issued(client, admin_token, session)
    store.inspect_answers = [_status("ended")]

    await _reconcile()

    cp = await _checkpoint(session, upload_id)
    assert (cp.stage, cp.last_error_category) == ("cancelled", "expired")
    assert await _writers(session) == set()


@pytest.mark.asyncio
async def test_progress_keeps_a_session_alive(client, admin_token, storage, store, session):
    upload_id = await _issued(client, admin_token, session)
    await session.execute(text("UPDATE upload_checkpoints SET progress_at = now() - interval '25 hours'"))
    await session.commit()
    store.inspect_answers = [_status("active", 1024)]

    await _reconcile()

    cp = await _checkpoint(session, upload_id)
    assert (cp.stage, cp.progress_bytes) == ("issued", 1024)
    assert cp.progress_at > datetime.now(timezone.utc) - timedelta(minutes=1)
    assert store.cancel_calls == 0


@pytest.mark.asyncio
async def test_an_abandoned_session_is_cancelled_with_the_provider(client, admin_token, storage, store, session):
    upload_id = await _issued(client, admin_token, session)
    await session.execute(
        text("UPDATE upload_checkpoints SET progress_bytes = 1024, progress_at = now() - interval '25 hours'")
    )
    await session.commit()
    store.inspect_answers = [_status("active", 1024)]
    store.cancel_answers = [_status("ended")]

    await _reconcile()

    cp = await _checkpoint(session, upload_id)
    assert (cp.stage, cp.last_error_category) == ("cancelled", "abandoned")
    assert store.cancel_calls == 1


# ---------------------------------------------------------------------------
# outcomes the provider cannot confirm: audited administrator release
# ---------------------------------------------------------------------------


async def _unconfirmed(client, token, session, store):
    upload_id = (await _initiate(client, token))["upload_id"]
    store.cancel_answers = [_status("unknown")] * 10
    await client.post(f"/api/files/uploads/{upload_id}/cancel", headers=_auth(token))
    for _ in range(5):
        await session.execute(text("UPDATE upload_checkpoints SET next_reconcile_at = now() - interval '1 second'"))
        await session.commit()
        await _reconcile()
    return upload_id


@pytest.mark.asyncio
async def test_repeated_unknown_answers_need_an_administrator(client, admin_token, storage, store, session):
    upload_id = await _unconfirmed(client, admin_token, session, store)
    cp = await _checkpoint(session, upload_id)
    assert (cp.stage, cp.last_error_category) == ("cancelling", "outcome_unknown")
    [writer] = await storage_write_admission.outstanding_writers(session)
    assert writer.needs_release is True


@pytest.mark.asyncio
async def test_an_administrator_releases_an_unconfirmed_writer_with_an_acknowledgement(
    client, admin_token, comp_bio_token, storage, store, session
):
    upload_id = await _unconfirmed(client, admin_token, session, store)
    url = f"/api/v1/infrastructure/storage/writes/{upload_id}/release"

    forbidden = await client.post(
        url, json={"reason": "Checked", "acknowledge_transfer_may_exist": True}, headers=_auth(comp_bio_token)
    )
    unacknowledged = await client.post(
        url, json={"reason": "Checked", "acknowledge_transfer_may_exist": False}, headers=_auth(admin_token)
    )
    released = await client.post(
        url,
        json={"reason": "Uploader confirmed the laptop is off", "acknowledge_transfer_may_exist": True},
        headers=_auth(admin_token),
    )

    assert forbidden.status_code == 403
    assert unacknowledged.status_code == 400
    assert released.status_code == 204
    cp = await _checkpoint(session, upload_id)
    assert (cp.stage, cp.release_reason) == ("cancelled", "Uploader confirmed the laptop is off")
    assert cp.released_at is not None and cp.released_by_user_id is not None
    assert await _writers(session) == set()
    audit = (
        await session.execute(text("SELECT details_json FROM audit_log WHERE action='upload_writer_released'"))
    ).scalar_one()
    assert audit["upload_id"] == upload_id


@pytest.mark.asyncio
async def test_a_writer_the_provider_reports_as_running_cannot_be_released(
    client, admin_token, storage, store, session
):
    upload_id = (await _initiate(client, admin_token))["upload_id"]
    response = await client.post(
        f"/api/v1/infrastructure/storage/writes/{upload_id}/release",
        json={"reason": "x", "acknowledge_transfer_may_exist": True},
        headers=_auth(admin_token),
    )
    assert response.status_code == 409
    assert (await _checkpoint(session, upload_id)).stage == "issued"


# ---------------------------------------------------------------------------
# presigned URLs issued before this release
# ---------------------------------------------------------------------------


async def _legacy(session, admin_user, *, expired_ago: timedelta, stage="issued") -> UploadCheckpoint:
    from tests.support.uploads import seed_signed_upload

    upload_id = await seed_signed_upload(org_id=admin_user.organization_id, user_id=admin_user.id, filename="x.txt")
    await session.execute(
        text(
            "UPDATE upload_checkpoints SET stage = :stage, grant_expires_at = :expires, "
            "next_reconcile_at = now() - interval '1 second' WHERE id = :id"
        ).bindparams(stage=stage, expires=datetime.now(timezone.utc) - expired_ago, id=upload_id)
    )
    await session.commit()
    return await _checkpoint(session, upload_id)


@pytest.mark.asyncio
async def test_a_presigned_url_counts_until_an_hour_after_it_expires(session, admin_user, storage, store):
    cp = await _legacy(session, admin_user, expired_ago=timedelta(minutes=30))
    await _reconcile()
    assert (await _checkpoint(session, cp.id)).stage == "issued"
    [writer] = await storage_write_admission.outstanding_writers(session)
    assert writer.settles_after == cp.grant_expires_at + timedelta(hours=1)
    assert writer.needs_release is False


@pytest.mark.asyncio
async def test_after_the_grace_period_a_stored_object_is_filed(session, admin_user, storage, store):
    cp = await _legacy(session, admin_user, expired_ago=timedelta(hours=2))
    store.objects.add(cp.source_uri)
    await _reconcile()
    assert (await _checkpoint(session, cp.id)).stage == "received"
    assert await _writers(session) == set()


@pytest.mark.asyncio
async def test_after_the_grace_period_a_cancelled_upload_object_is_removed(session, admin_user, storage, store):
    cp = await _legacy(session, admin_user, expired_ago=timedelta(hours=2), stage="cancelling")
    store.objects.add(cp.source_uri)
    await _reconcile()
    assert (await _checkpoint(session, cp.id)).stage == "cancelled"
    assert store.deleted == [cp.source_uri]


@pytest.mark.asyncio
async def test_after_the_grace_period_no_object_means_the_outcome_is_unknown(session, admin_user, storage, store):
    cp = await _legacy(session, admin_user, expired_ago=timedelta(hours=2))
    await _reconcile()
    cp = await _checkpoint(session, cp.id)
    assert (cp.stage, cp.last_error_category) == ("issued", "outcome_unknown")
    [writer] = await storage_write_admission.outstanding_writers(session)
    assert writer.needs_release is True


def test_reconciliation_runs_every_minute():
    from app.tasks.jobs import PERIODIC_JOBS

    job = next(j for j in PERIODIC_JOBS if j.role == "upload-session-reconcile")
    assert (job.first_delay, job.interval) == (60, 60)


# ---------------------------------------------------------------------------
# the attention list shows uploads still settling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_attention_list_shows_stopping_and_unconfirmed_uploads(
    client, admin_token, bench_token, storage, store, session
):
    unconfirmed = await _unconfirmed(client, admin_token, session, store)
    stopping = (await _initiate(client, admin_token))["upload_id"]
    store.cancel_answers = [_status("unknown")]
    await client.post(f"/api/files/uploads/{stopping}/cancel", headers=_auth(admin_token))
    running = (await _initiate(client, admin_token))["upload_id"]

    rows = (await client.get("/api/files/uploads/pending", headers=_auth(admin_token))).json()["uploads"]
    states = {r["upload_id"]: r["state"] for r in rows}
    assert states == {stopping: "stopping", unconfirmed: "unconfirmed"}
    assert running not in states
    assert all("session" not in str(r).lower() or "SECRET" not in str(r) for r in rows)
    bench_rows = (await client.get("/api/files/uploads/pending", headers=_auth(bench_token))).json()["uploads"]
    assert bench_rows == []
