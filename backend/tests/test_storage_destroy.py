"""Retryable storage destruction (storage lifecycle 11G; approved D3, D5; mockups M7, M8, M11).

Destroy acts only on buckets established as this installation's, pauses uploads and waits for
running ones before deleting anything, treats a provider-confirmed missing bucket as already
removed, stops on a refused or failing bucket with what was done recorded, and records byte loss
only for the files in buckets it actually emptied or found gone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services import storage_identity
from app.services.storage_identity import StoreHealth, StoreReport
from app.services.storage_write_admission import OutstandingWriter
from app.services.terraform_executor import TerraformProgressEvent

RAW, RESULTS = "bioaf-raw-acme-abc123", "bioaf-results-acme-abc123"


async def _config(session, **kv):
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


async def _get(session, key):
    return (await session.execute(text("SELECT value FROM platform_config WHERE key=:k").bindparams(k=key))).scalar()


@pytest_asyncio.fixture
async def deployed(session, admin_user):
    await _config(
        session,
        compute_deployed="false",
        storage_deployed="true",
        raw_bucket_name=RAW,
        results_bucket_name=RESULTS,
        storage_stack_uid="abc123",
    )
    return admin_user.id


def _health(table: dict[str, list[StoreHealth]]):
    """inspect_bucket answering from a per-bucket sequence (the last answer repeats)."""

    async def inspect(session, store, bucket):
        answers = table[bucket]
        health = answers.pop(0) if len(answers) > 1 else answers[0]
        return StoreReport(store, bucket, health, "discovered")

    return patch.object(storage_identity, "inspect_bucket", side_effect=inspect)


async def _destroy_ok(*_a):
    yield TerraformProgressEvent(event_type="apply_complete", message="done")


async def _run(session, user_id, *, empty=None, terraform=_destroy_ok, drain=None):
    from app.services.stack_deployment import destroy_storage

    calls = {"empty": [], "terraform": 0}

    async def default_empty(_s, bucket):
        calls["empty"].append(bucket)
        return 3

    async def tf(*a):
        calls["terraform"] += 1
        async for event in terraform(*a):
            yield event

    events = []
    with (
        patch("app.services.stack_deployment._empty_gcs_bucket", side_effect=empty or default_empty),
        patch("app.services.stack_deployment._run_destroy", side_effect=tf),
        patch("app.services.storage_write_admission.drain", AsyncMock(return_value=drain or [])),
        patch("app.services.stack_deployment.asyncio.sleep", AsyncMock()),
    ):
        async for event in destroy_storage(session, user_id=user_id):
            events.append(event)
    await session.commit()
    return events, calls


def _steps(events):
    return {e.extra["key"]: e.extra for e in events if e.event_type == "step"}


@pytest.mark.asyncio
async def test_a_bucket_already_gone_is_skipped_and_destroy_finishes(session, deployed):
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.NOT_FOUND]}):
        events, calls = await _run(session, deployed)

    steps = _steps(events)
    assert (steps[f"empty:{RESULTS}"]["state"], steps[f"empty:{RESULTS}"]["detail"]) == (
        "already_removed",
        "Google Cloud reports this bucket does not exist.",
    )
    assert calls["empty"] == [RAW]
    assert calls["terraform"] == 1
    assert events[-1].event_type == "stack_complete"
    assert events[-1].message == (
        "Storage destroyed. 2 buckets emptied and removed. 1 had already been removed outside bioAF. "
        "A new deployment will create new buckets with new names."
    )


@pytest.mark.asyncio
async def test_a_bucket_that_disappears_while_emptying_counts_as_removed(session, deployed):
    async def empty(_s, bucket):
        if bucket == RAW:
            raise RuntimeError("404")
        return 0

    with _health({RAW: [StoreHealth.AVAILABLE, StoreHealth.NOT_FOUND], RESULTS: [StoreHealth.AVAILABLE]}):
        events, calls = await _run(session, deployed, empty=empty)

    assert _steps(events)[f"empty:{RAW}"]["state"] == "already_removed"
    assert events[-1].event_type == "stack_complete"


@pytest.mark.asyncio
async def test_an_unresponsive_provider_is_retried_then_stops_with_nothing_more_deleted(session, deployed):
    # Deletion resumes only once the provider answers again and the bucket is still established;
    # while it does not answer, bioAF keeps checking (3 attempts) and deletes nothing more.
    tries = {"n": 0}

    async def empty(_s, bucket):
        tries["n"] += 1
        raise RuntimeError("503")

    with _health(
        {RAW: [StoreHealth.AVAILABLE, StoreHealth.NOT_RESPONDING], RESULTS: [StoreHealth.AVAILABLE]}
    ) as inspect:
        events, calls = await _run(session, deployed, empty=empty)

    assert tries["n"] == 1
    assert inspect.call_count == 2 + 3  # preflight for both buckets, then one check per attempt
    assert calls["terraform"] == 0
    error = events[-1]
    assert error.event_type == "stack_error"
    assert (
        error.extra["step_detail"]
        == "Google Cloud did not respond after several tries. Nothing more was deleted from it."
    )
    assert await _get(session, "storage_deployed") == "true"
    assert await _get(session, "storage_stack_uid") == "abc123"


@pytest.mark.asyncio
async def test_emptying_resumes_when_the_provider_answers_again(session, deployed):
    tries = {"n": 0}

    async def empty(_s, bucket):
        tries["n"] += 1
        if tries["n"] == 1:
            raise RuntimeError("503")
        return 1

    table = {RAW: [StoreHealth.AVAILABLE, StoreHealth.NOT_RESPONDING, StoreHealth.AVAILABLE]}
    table[RESULTS] = [StoreHealth.AVAILABLE]
    with _health(table):
        events, _ = await _run(session, deployed, empty=empty)

    assert tries["n"] == 3  # raw failed, raw again, results
    assert events[-1].event_type == "stack_complete"


@pytest.mark.asyncio
async def test_unconfirmed_buckets_stop_destroy_before_anything(session, deployed):
    with _health({RAW: [StoreHealth.NOT_CONFIRMED], RESULTS: [StoreHealth.AVAILABLE]}):
        events, calls = await _run(session, deployed)

    assert events[-1].message == (
        "Storage destroy is unavailable until an administrator confirms the storage buckets. Nothing was changed."
    )
    assert calls == {"empty": [], "terraform": 0}
    assert await _get(session, "storage_writes_paused") is None


@pytest.mark.asyncio
async def test_another_installations_bucket_is_never_touched(session, deployed):
    with _health({RAW: [StoreHealth.WRONG_OWNER], RESULTS: [StoreHealth.AVAILABLE]}):
        events, calls = await _run(session, deployed)
    assert events[-1].extra["category"] == "wrong_owner"
    assert calls == {"empty": [], "terraform": 0}


@pytest.mark.asyncio
async def test_running_uploads_stop_destroy_before_deleting_anything(session, deployed):
    started = datetime(2026, 11, 21, 15, 58, tzinfo=timezone.utc)
    writers = [
        OutstandingWriter("u1", deployed, 42, "a.fastq.gz", started, "proxied"),
        OutstandingWriter("u2", deployed, 42, "b.fastq.gz", started, "proxied"),
    ]
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, calls = await _run(session, deployed, drain=writers)

    error = events[-1]
    assert error.message == (
        "2 uploads were still in progress after 30 seconds, so nothing was deleted. "
        "New uploads stay paused until you allow them or run Destroy Storage again."
    )
    assert error.extra["effects"]["uploads"] == [
        {
            "user_id": deployed,
            "experiment_id": 42,
            "files": 2,
            "started_at": started.isoformat(),
            "name": None,
            "experiment": None,
        }
    ]
    assert calls == {"empty": [], "terraform": 0}
    assert await _get(session, "storage_writes_paused") is not None


@pytest.mark.asyncio
async def test_an_unconfirmed_signed_upload_is_uncertain(session, deployed):
    writers = [OutstandingWriter("u1", deployed, None, "a.txt", datetime.now(timezone.utc), "signed")]
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, _ = await _run(session, deployed, drain=writers)
    assert events[-1].message.startswith(
        "bioAF could not confirm that every upload had finished, so nothing was deleted."
    )


@pytest.mark.asyncio
async def test_byte_loss_is_recorded_only_for_emptied_buckets(session, deployed):
    await session.execute(
        text(
            "INSERT INTO files (organization_id, filename, storage_uri, gcs_uri, file_type, storage_deleted, source_type) "
            "SELECT o.id, f.name, f.uri, f.uri, 'other', false, 'upload' FROM organizations o, "
            "(VALUES ('in-raw.txt', :raw), ('elsewhere.txt', 'gs://some-other-bucket/x.txt')) AS f(name, uri) "
            "LIMIT 2"
        ).bindparams(raw=f"gs://{RAW}/experiments/1/in-raw.txt")
    )
    await session.commit()

    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        await _run(session, deployed)

    rows = dict((await session.execute(text("SELECT filename, storage_deleted FROM files"))).all())
    assert rows == {"in-raw.txt": True, "elsewhere.txt": False}


@pytest.mark.asyncio
async def test_a_confirmed_destroy_reopens_uploads_and_retires_the_generation(session, deployed):
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, _ = await _run(session, deployed)
    assert events[-1].event_type == "stack_complete"
    assert await _get(session, "storage_writes_paused") is None
    assert await _get(session, "storage_stack_uid") is None


@pytest.mark.asyncio
async def test_a_failed_terraform_destroy_keeps_settings_and_uploads_paused(session, deployed):
    async def tf_fails(*_a):
        yield TerraformProgressEvent(event_type="apply_error", message="boom")

    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, _ = await _run(session, deployed, terraform=tf_fails)
    assert events[-1].event_type == "stack_error"
    assert await _get(session, "storage_stack_uid") == "abc123"
    assert await _get(session, "storage_writes_paused") is not None


@pytest.mark.asyncio
async def test_byte_loss_is_recorded_for_each_object_deleted_before_a_failure(session, deployed, admin_user):
    """Objects confirmed deleted stay recorded as deleted when a later object in the bucket fails."""
    from types import SimpleNamespace

    from app.services.stack_deployment import destroy_storage

    await _config(session, results_bucket_name="null")
    for name in ("a", "b"):
        await session.execute(
            text(
                "INSERT INTO files (organization_id, filename, storage_uri, gcs_uri, file_type, storage_deleted, "
                "source_type) VALUES (:org, :name, :uri, :uri, 'other', false, 'upload')"
            ).bindparams(org=admin_user.organization_id, name=name, uri=f"gs://{RAW}/{name}")
        )
    await session.commit()
    adapter = AsyncMock()
    adapter.build_uri = lambda bucket, key: f"gs://{bucket}/{key}"
    adapter.list_objects.return_value = [
        SimpleNamespace(storage_uri=f"gs://{RAW}/{name}", provider_details={"generation": 1}) for name in ("a", "b")
    ]
    adapter.delete.side_effect = [None, PermissionError("denied after the first object")]

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        _health({RAW: [StoreHealth.AVAILABLE, StoreHealth.NO_ACCESS]}),
        patch("app.services.storage_write_admission.drain", AsyncMock(return_value=[])),
    ):
        events = [e async for e in destroy_storage(session, user_id=deployed)]

    error = events[-1]
    assert error.event_type == "stack_error"
    assert error.extra["step_detail"] == "bioAF is not allowed to read this bucket. Nothing more was deleted from it."
    rows = dict((await session.execute(text("SELECT filename, storage_deleted FROM files"))).all())
    assert rows == {"a": True, "b": False}


@pytest.mark.parametrize("health", [StoreHealth.CANNOT_CHECK, StoreHealth.NO_ACCESS])
@pytest.mark.asyncio
async def test_nothing_is_deleted_from_a_bucket_whose_identity_is_not_established(session, deployed, health):
    with _health({RAW: [health], RESULTS: [StoreHealth.AVAILABLE]}):
        events, calls = await _run(session, deployed)
    assert events[-1].event_type == "stack_error"
    assert calls == {"empty": [], "terraform": 0}


@pytest.mark.asyncio
async def test_a_refusal_for_settling_uploads_says_when_to_retry(session, deployed):
    later = datetime(2026, 11, 21, 17, 5, tzinfo=timezone.utc)
    sooner = datetime(2026, 11, 21, 16, 45, tzinfo=timezone.utc)
    started = datetime(2026, 11, 21, 15, 58, tzinfo=timezone.utc)
    writers = [
        OutstandingWriter("u1", deployed, None, "a.txt", started, "signed", state="sending", settles_after=later),
        OutstandingWriter("u2", deployed, None, "b.txt", started, "session", state="stopping", settles_after=sooner),
    ]
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, calls = await _run(session, deployed, drain=writers)

    error = events[-1]
    assert error.message.startswith("bioAF could not confirm that every upload had finished, so nothing was deleted.")
    assert error.extra["effects"]["retry_after"] == later.isoformat()
    assert error.extra["effects"]["unconfirmed"] == []
    assert calls == {"empty": [], "terraform": 0}


@pytest.mark.asyncio
async def test_a_refusal_lists_uploads_an_administrator_must_release(session, deployed):
    started = datetime(2026, 11, 21, 15, 58, tzinfo=timezone.utc)
    writers = [
        OutstandingWriter("u1", deployed, 42, "a.txt", started, "session", state="unconfirmed", reference="UPL-ABC123"),
    ]
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, _ = await _run(session, deployed, drain=writers)

    effects = events[-1].extra["effects"]
    assert effects["retry_after"] is None
    assert effects["unconfirmed"] == [
        {
            "upload_id": "u1",
            "reference": "UPL-ABC123",
            "filename": "a.txt",
            "user_id": deployed,
            "name": None,
            "started_at": started.isoformat(),
        }
    ]


@pytest.mark.asyncio
async def test_a_refusal_releases_the_operation_lock(session, admin_user, deployed):
    """Waiting for uploads must not keep every infrastructure action unavailable."""
    from app.services import infrastructure_operations as ops
    from app.services.stack_deployment import destroy_storage

    writers = [OutstandingWriter("u1", deployed, None, "a.txt", datetime.now(timezone.utc), "session")]
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(admin_user.id))

    async def body(handle):
        return await ops.drive_stack(handle, lambda s: destroy_storage(s, user_id=deployed))

    with (
        _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}),
        patch("app.services.storage_write_admission.drain", AsyncMock(return_value=writers)),
    ):
        await ops.run_operation(op_id, body)

    session.expire_all()
    assert await ops.active_operation(session) is None
    assert await _get(session, "storage_writes_paused") is not None


@pytest.mark.asyncio
async def test_bioafs_own_work_is_named_by_activity(session, deployed):
    started = datetime(2026, 11, 21, 15, 58, tzinfo=timezone.utc)
    writers = [
        OutstandingWriter("background:data export:1", None, None, "data export", started, "background"),
        OutstandingWriter("background:reference import:2", None, None, "reference import", started, "background"),
    ]
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, _ = await _run(session, deployed, drain=writers)
    error = events[-1]
    assert error.message == (
        "bioAF is still saving files. Nothing was deleted. Try Destroy Storage again when this work has finished. "
        "New uploads stay paused until you allow them or run Destroy Storage again."
    )
    assert error.extra["effects"]["activities"] == [
        {"activity": "Creating a data export", "started_at": started.isoformat()},
        {"activity": "Importing reference data", "started_at": started.isoformat()},
    ]
    assert error.extra["effects"]["uploads"] == []


@pytest.mark.asyncio
async def test_uploads_and_bioafs_own_work_together(session, deployed):
    started = datetime(2026, 11, 21, 15, 58, tzinfo=timezone.utc)
    writers = [
        OutstandingWriter("u1", deployed, None, "a.txt", started, "proxied"),
        OutstandingWriter("background:thumbnail:1", None, None, "thumbnail", started, "background"),
    ]
    with _health({RAW: [StoreHealth.AVAILABLE], RESULTS: [StoreHealth.AVAILABLE]}):
        events, _ = await _run(session, deployed, drain=writers)
    assert events[-1].message == (
        "Files are still being uploaded or saved. Nothing was deleted. "
        "New uploads stay paused until you allow them or run Destroy Storage again."
    )
