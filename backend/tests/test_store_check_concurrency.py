"""The pre-upload store check never poisons the upload's transaction (found by live testing).

On a fresh installation, overlapping first uploads each discover the bucket's identity and insert
the same row; the losers hit a unique conflict. That conflict must stay inside the check: the
upload goes on (re-reading the identity the winner recorded), never failing later with a
PendingRollbackError.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.models.storage_identity import StorageIdentity
from app.services import storage_identity, storage_write_admission
from app.services.storage_identity import StoreHealth, StoreReport

pytestmark = pytest.mark.asyncio


@pytest.fixture
def cloud_buckets():
    class Cloud:
        is_local = False

    async def buckets(session):
        return {"ingest": "bioaf-ingest-x"}

    with (
        patch.object(storage_identity, "_adapter", lambda: Cloud()),
        patch.object(storage_identity, "_configured_buckets", buckets),
    ):
        yield


def _identity():
    return StorageIdentity(store="ingest", bucket_name="bioaf-ingest-x", storage_uid="g1", basis="discovered")


async def test_a_concurrent_identity_insert_leaves_the_upload_transaction_usable(session, admin_user, cloud_buckets):
    session.add(_identity())  # what the winning request recorded first
    await session.commit()
    calls = []

    async def racing_inspect(s, store, bucket):
        calls.append(store)
        if len(calls) == 1:
            s.add(_identity())  # this request's own discovery of the same identity
            await s.flush()  # raises IntegrityError, as the losing request did live
        return StoreReport(store, bucket, StoreHealth.AVAILABLE, "discovered")

    with patch.object(storage_identity, "inspect_bucket", racing_inspect):
        await storage_write_admission.require_store_usable(session, "ingest")

    assert calls == ["ingest", "ingest"]  # re-read after the conflict
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1  # not poisoned
    assert len((await session.execute(select(StorageIdentity))).scalars().all()) == 1


async def test_any_check_failure_leaves_the_upload_transaction_usable(session, admin_user, cloud_buckets):
    async def broken_inspect(s, store, bucket):
        await s.execute(text("SELECT 1/0"))  # a database error inside the check

    with patch.object(storage_identity, "inspect_bucket", broken_inspect):
        await storage_write_admission.require_store_usable(session, "ingest")
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1


async def test_a_definitely_unusable_store_still_refuses(session, admin_user, cloud_buckets):
    async def missing(s, store, bucket):
        return StoreReport(store, bucket, StoreHealth.NOT_FOUND)

    with (
        patch.object(storage_identity, "inspect_bucket", missing),
        pytest.raises(storage_write_admission.UploadStorageUnavailableError),
    ):
        await storage_write_admission.require_store_usable(session, "ingest")
