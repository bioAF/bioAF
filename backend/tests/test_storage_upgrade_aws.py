"""Upgrading an existing AWS installation, with only the permissions the installer grants.

The installer gives bioAF ``s3:*`` on ``bioaf-*`` buckets and nothing account-wide, so listing every
bucket is refused. Ownership is checked with HeadBucket's ExpectedBucketOwner against the recorded
account; bioAF's marker tag (added by an approved update, or at deployment) tells a bucket apart from
one recreated under the same name. A legacy bucket keeps working (uploads, Plot Archive) and needs
one administrator confirmation before Destroy Storage. A bucket policy edit changes nothing bioAF
relies on. These run the real S3 adapter against a fake S3 that enforces those permissions.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import StateError
from app.services import storage_identity
from app.services.storage_identity import StoreHealth

ACCOUNT = "123456789012"
RAW = "bioaf-raw-acme-abc123"


def _error(code: str, status: int, operation: str):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation)


class InstallerGrantedS3:
    """Bucket-level calls on bioaf-* buckets succeed; account-wide listing is refused."""

    def __init__(self):
        self.buckets: dict[str, dict] = {}

    def add(self, name, *, owner=ACCOUNT, tags=None, created="2026-03-02"):
        self.buckets[name] = {"owner": owner, "tags": dict(tags or {}), "created": created}

    def head_bucket(self, Bucket, ExpectedBucketOwner=None):
        bucket = self.buckets.get(Bucket)
        if bucket is None:
            raise _error("404", 404, "HeadBucket")
        if ExpectedBucketOwner is not None and ExpectedBucketOwner != bucket["owner"]:
            raise _error("403", 403, "HeadBucket")
        return {}

    def get_bucket_tagging(self, Bucket):
        tags = self.buckets[Bucket]["tags"]
        if not tags:
            raise _error("NoSuchTagSet", 404, "GetBucketTagging")
        return {"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]}

    def list_buckets(self):
        raise _error("AccessDenied", 403, "ListBuckets")

    def put_bucket_policy(self, Bucket, Policy):
        # AWS may report a new creation date after this; bioAF does not use it.
        self.buckets[Bucket]["created"] = "2026-11-21"


@pytest_asyncio.fixture
async def aws(session, admin_user):
    for key, value in {
        "cloud_provider": "aws",
        "aws_account_id": ACCOUNT,
        "raw_bucket_name": RAW,
        "storage_stack_uid": "abc123",
        "storage_deployed": "true",
        "compute_deployed": "false",
    }.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()
    cloud = InstallerGrantedS3()
    adapter = S3StorageProvider.__new__(S3StorageProvider)
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch.object(S3StorageProvider, "is_local", property(lambda self: False)),
        patch.object(adapter, "_get_credentials", AsyncMock(return_value=None)),
        patch.object(adapter, "_get_s3_client", return_value=cloud),
    ):
        yield cloud, admin_user.id


async def _raw(session):
    report = next(r for r in await storage_identity.inspect_stores(session) if r.store == "raw")
    await session.commit()
    return report


async def _add_markers_by_update(session, cloud):
    """What an approved Check for Infrastructure Updates does: record markers, tag the bucket."""
    from app.services.storage_generation import ensure_markers

    markers = await ensure_markers(session, origin="update")
    await session.commit()
    cloud.buckets[RAW]["tags"]["bioaf_marker"] = markers["markers"]["raw"]


@pytest.mark.asyncio
async def test_a_legacy_bucket_keeps_working_and_asks_for_one_confirmation(session, aws):
    from app.services.plot_archive_service import PlotArchiveService

    cloud, user_id = aws
    cloud.add(RAW, tags={"managed_by": "bioaf", "storage_uid": "abc123"})

    report = await _raw(session)
    assert (report.health, report.usable, report.trusted) == (StoreHealth.NOT_CONFIRMED, True, False)
    # Plot Archive keeps scanning a readable store that is only missing confirmation.
    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES ('results_bucket_name', :b)").bindparams(b=RAW)
    )
    await session.commit()
    with patch.object(PlotArchiveService, "scan_and_index", AsyncMock()) as scan:
        await PlotArchiveService.scheduled_scan(session, force=True)
    scan.assert_awaited_once()
    # Without bioAF's marker, ownership alone cannot be confirmed.
    with pytest.raises(StateError, match="Check for Infrastructure Updates"):
        await storage_identity.enroll_stores(session, user_id, ["raw"], reason="legacy")


@pytest.mark.asyncio
async def test_after_the_update_adds_the_marker_one_confirmation_establishes_the_bucket(session, aws):
    cloud, user_id = aws
    cloud.add(RAW, tags={"managed_by": "bioaf", "storage_uid": "abc123"})
    await _add_markers_by_update(session, cloud)

    assert (await _raw(session)).health is StoreHealth.NOT_CONFIRMED
    [report] = await storage_identity.enroll_stores(session, user_id, ["raw"], reason="Checked in the AWS console")
    await session.commit()
    assert report.health is StoreHealth.AVAILABLE
    assert (await _raw(session)).trusted is True


@pytest.mark.asyncio
async def test_a_bucket_policy_edit_keeps_the_bucket_established(session, aws):
    cloud, user_id = aws
    cloud.add(RAW, tags={"managed_by": "bioaf", "storage_uid": "abc123"})
    await _add_markers_by_update(session, cloud)
    await storage_identity.enroll_stores(session, user_id, ["raw"], reason="Checked")
    await session.commit()

    cloud.put_bucket_policy(Bucket=RAW, Policy="{}")

    assert (await _raw(session)).health is StoreHealth.AVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recreated_tags, health",
    [
        ({"managed_by": "bioaf", "storage_uid": "abc123", "bioaf_marker": "0000000000000000"}, StoreHealth.WRONG_OWNER),
        ({"managed_by": "bioaf", "storage_uid": "abc123"}, StoreHealth.CANNOT_CHECK),
    ],
)
async def test_a_recreated_bucket_is_never_trusted_for_destruction(session, aws, recreated_tags, health):
    cloud, user_id = aws
    cloud.add(RAW, tags={"managed_by": "bioaf", "storage_uid": "abc123"})
    await _add_markers_by_update(session, cloud)
    await storage_identity.enroll_stores(session, user_id, ["raw"], reason="Checked")
    await session.commit()

    cloud.add(RAW, tags=recreated_tags)  # deleted and recreated under the same name

    report = await _raw(session)
    assert report.health is health
    assert report.trusted is False


@pytest.mark.asyncio
async def test_another_accounts_bucket_with_the_same_name_is_a_known_mismatch(session, aws):
    cloud, user_id = aws
    cloud.add(RAW, owner="999999999999", tags={"managed_by": "bioaf", "storage_uid": "abc123"})
    assert (await _raw(session)).health is StoreHealth.WRONG_OWNER
    with pytest.raises(StateError, match="another installation"):
        await storage_identity.enroll_stores(session, user_id, ["raw"], reason="trust me")


@pytest.mark.asyncio
async def test_destroy_waits_for_confirmation_and_then_proceeds(session, aws):
    from app.services.stack_deployment import destroy_storage

    cloud, user_id = aws
    cloud.add(RAW, tags={"managed_by": "bioaf", "storage_uid": "abc123"})

    refused = [e async for e in destroy_storage(session, user_id=user_id)]
    await session.commit()
    assert refused[-1].extra["category"] == "not_confirmed"

    await _add_markers_by_update(session, cloud)
    await storage_identity.enroll_stores(session, user_id, ["raw"], reason="Checked")
    await session.commit()

    async def destroyed(*_a):
        from app.services.terraform_executor import TerraformProgressEvent

        yield TerraformProgressEvent(event_type="apply_complete", message="done")

    with (
        patch("app.services.stack_deployment._empty_gcs_bucket", AsyncMock(return_value=0)),
        patch("app.services.stack_deployment._run_destroy", side_effect=destroyed),
        patch("app.services.storage_write_admission.drain", AsyncMock(return_value=[])),
    ):
        events = [e async for e in destroy_storage(session, user_id=user_id)]
    await session.commit()
    assert events[-1].event_type == "stack_complete"
    markers = await session.scalar(text("SELECT value FROM platform_config WHERE key = 'storage_bucket_markers'"))
    assert markers in (None, "null") or json.loads(markers) is None
