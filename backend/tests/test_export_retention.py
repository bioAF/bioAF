"""The hourly export cleanup: 48 h live retention and the legacy activation gate (plan 24 decision 7)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.adapters import registry
from app.adapters.models import StorageStore, StoredObject
from app.platform.platform_config_service import PlatformConfigService
from app.services.data_export import retention

UTC = timezone.utc
NEW = "exports/7/0b0c7d4e-1f2a-4b3c-8d9e-0123456789ab.zip"
NEW2 = "exports/7/1b0c7d4e-1f2a-4b3c-8d9e-0123456789ab.zip"
LEGACY = "exports/7/20261101_120000_experiment_3.zip"


async def _fresh_session():
    from app import database

    return database.async_session_factory()


async def _activation(session) -> str | None:
    return await PlatformConfigService.get(session, retention.ACTIVATION_KEY)


class FakeStore:
    """A cloud-shaped store: listing returns what was put, with the given upload times."""

    def __init__(self, scheme: str = "s3"):
        self.scheme = scheme
        self.objects: dict[str, datetime | None] = {}
        self.deleted: list[str] = []
        self.listed: list[str] = []
        self.fail_list = False
        self.fail_delete: set[str] = set()

    async def resolve_uri(self, store, key):
        assert store is StorageStore.CONFIG_BACKUPS
        return f"{self.scheme}://config/{key}"

    def parse_uri(self, uri):
        bucket, _, key = uri.split("://", 1)[1].partition("/")
        return bucket, key

    async def list_objects(self, prefix, **kwargs):
        self.listed.append(prefix)
        if self.fail_list:
            raise RuntimeError("listing failed")
        return [
            StoredObject(filename=k.rsplit("/", 1)[-1], storage_uri=f"{self.scheme}://config/{k}", uploaded_at=t)
            for k, t in self.objects.items()
            if k.startswith(prefix.split("://config/", 1)[1])
        ]

    async def delete(self, uri, **kwargs):
        if uri in self.fail_delete:
            raise RuntimeError("delete failed")
        self.deleted.append(uri)

    def keys_deleted(self):
        return sorted(u.split("://config/", 1)[1] for u in self.deleted)


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(registry, "_storage_adapter", fake)
    return fake


def _ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


# -- Activation ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_pass_records_activation_once_and_commits_it(session, store):
    await retention.run(session)

    async with await _fresh_session() as other:
        stored = await _activation(other)
    assert stored is not None
    assert abs((datetime.fromisoformat(stored) - datetime.now(UTC)).total_seconds()) < 60

    await retention.run(session)
    async with await _fresh_session() as other:
        assert await _activation(other) == stored


@pytest.mark.asyncio
async def test_an_existing_activation_survives_passes_and_new_sessions(session, store):
    original = _ago(100).isoformat()
    await PlatformConfigService.set(session, retention.ACTIVATION_KEY, original)
    await session.commit()

    for _ in range(3):
        async with await _fresh_session() as fresh:
            await retention.run(fresh)

    async with await _fresh_session() as other:
        assert await _activation(other) == original


@pytest.mark.asyncio
async def test_a_listing_failure_does_not_roll_back_activation(session, store):
    store.fail_list = True

    with pytest.raises(RuntimeError):  # the job runner reports it; the next hourly pass retries
        await retention.run(session)

    async with await _fresh_session() as other:
        assert await _activation(other) is not None


@pytest.mark.parametrize("value", ["not-a-time", "2026-11-01T00:00:00", ""])
@pytest.mark.asyncio
async def test_an_invalid_activation_blocks_legacy_deletion_and_is_never_overwritten(session, store, value, caplog):
    await PlatformConfigService.set(session, retention.ACTIVATION_KEY, value)
    await session.commit()
    store.objects = {LEGACY: _ago(1000), NEW: _ago(49)}

    await retention.run(session)

    assert store.keys_deleted() == [NEW]
    async with await _fresh_session() as other:
        assert await _activation(other) == value
    assert "export retention activation" in caplog.text.lower()
    assert value not in caplog.text or value == ""


@pytest.mark.asyncio
async def test_a_future_activation_keeps_legacy_objects(session, store):
    await PlatformConfigService.set(
        session, retention.ACTIVATION_KEY, (datetime.now(UTC) + timedelta(days=3)).isoformat()
    )
    await session.commit()
    store.objects = {LEGACY: _ago(1000)}

    await retention.run(session)

    assert store.deleted == []


@pytest.mark.asyncio
async def test_failing_to_persist_activation_skips_legacy_deletion_only(session, store, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("database read-only")

    monkeypatch.setattr(PlatformConfigService, "insert_if_absent", broken)
    store.objects = {LEGACY: _ago(1000), NEW: _ago(49)}

    await retention.run(session)

    assert store.keys_deleted() == [NEW]


# -- Eligibility ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("activated_hours_ago", "objects", "deleted"),
    [
        (100, {NEW: 47}, []),
        (100, {NEW: 49}, [NEW]),
        (25, {LEGACY: 49}, [LEGACY]),
        (23, {LEGACY: 1000}, []),
        (25, {LEGACY: 47}, []),
        (100, {NEW: 49, NEW2: 1, LEGACY: 49}, [LEGACY, NEW]),
    ],
)
@pytest.mark.asyncio
async def test_eligibility(session, store, activated_hours_ago, objects, deleted):
    await PlatformConfigService.set(session, retention.ACTIVATION_KEY, _ago(activated_hours_ago).isoformat())
    await session.commit()
    store.objects = {k: _ago(h) for k, h in objects.items()}

    await retention.run(session)

    assert store.keys_deleted() == sorted(deleted)


@pytest.mark.asyncio
async def test_unknown_names_and_unknown_times_are_kept(session, store):
    await PlatformConfigService.set(session, retention.ACTIVATION_KEY, _ago(100).isoformat())
    await session.commit()
    store.objects = {
        "exports/7/notes.txt": _ago(1000),
        "exports/7/sub/0b0c7d4e-1f2a-4b3c-8d9e-0123456789ab.zip": _ago(1000),
        NEW: None,
        NEW2: _ago(1000).replace(tzinfo=None),
    }

    await retention.run(session)

    assert store.deleted == []


@pytest.mark.asyncio
async def test_only_the_exports_prefix_of_the_config_store_is_listed(session, store):
    await retention.run(session)

    assert store.listed == ["s3://config/exports/"]


@pytest.mark.asyncio
async def test_one_failed_delete_does_not_stop_the_pass(session, store):
    await PlatformConfigService.set(session, retention.ACTIVATION_KEY, _ago(100).isoformat())
    await session.commit()
    store.objects = {NEW: _ago(49), NEW2: _ago(49)}
    store.fail_delete = {f"s3://config/{NEW}"}

    await retention.run(session)

    assert store.keys_deleted() == [NEW2]


@pytest.mark.asyncio
async def test_an_install_without_a_config_store_is_skipped_quietly(session, monkeypatch):
    from app.exceptions import ValidationError

    class NoBucket(FakeStore):
        async def resolve_uri(self, store, key):
            raise ValidationError("No bucket configured for store 'config_backups'")

    monkeypatch.setattr(registry, "_storage_adapter", NoBucket())

    await retention.run(session)


# -- An actual local store -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_local_store_deletes_by_its_own_upload_times(session, tmp_path, monkeypatch):
    from app.adapters.storage import gcs

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = registry.get_storage_adapter()
    await PlatformConfigService.set(session, retention.ACTIVATION_KEY, _ago(100).isoformat())
    await session.commit()
    uris = {}
    for key, hours in {NEW: 49, NEW2: 2, LEGACY: 49}.items():
        uri = await adapter.resolve_uri(StorageStore.CONFIG_BACKUPS, key)
        await adapter.write_bytes(uri, b"zip")
        stamp = _ago(hours).timestamp()
        os.utime(adapter._local_path(uri), (stamp, stamp))
        uris[key] = uri
    other = await adapter.resolve_uri(StorageStore.CONFIG_BACKUPS, "backups/old.json")
    await adapter.write_bytes(other, b"{}")
    os.utime(adapter._local_path(other), (0, 0))

    await retention.run(session)

    assert not await adapter.exists(uris[NEW])
    assert not await adapter.exists(uris[LEGACY])
    assert await adapter.exists(uris[NEW2])
    assert await adapter.exists(other)


def test_the_job_runs_hourly_under_its_existing_role():
    from app.tasks import jobs

    [job] = [j for j in jobs.PERIODIC_JOBS if j.role == "export-cleanup"]
    assert job.interval == 3600
