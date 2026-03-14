import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def configured_ingest_bucket(session, admin_user):
    """Insert ingest_bucket_name into platform_config for this org's deployment."""
    from app.models.component import PlatformConfig

    cfg = PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test-abc123")
    session.add(cfg)
    await session.flush()
    await session.commit()
    return "bioaf-ingest-test-abc123"


@pytest.mark.asyncio
async def test_initiate_upload_requires_bucket_config(client, admin_token):
    """Upload initiate must return 400 when ingest_bucket_name is not configured."""
    resp = await client.post(
        "/api/files/upload/initiate",
        json={"filename": "sample.fastq.gz", "expected_size_bytes": 1_000_000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "bucket" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_initiate_upload_uses_configured_bucket(client, admin_token, configured_ingest_bucket):
    """Upload initiate must use the bucket name stored in platform_config."""
    resp = await client.post(
        "/api/files/upload/initiate",
        json={"filename": "sample.fastq.gz", "expected_size_bytes": 1_000_000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert configured_ingest_bucket in data["gcs_uri"]


@pytest.mark.asyncio
async def test_initiate_upload_rejects_null_bucket(client, admin_token, session):
    """platform_config row with value 'null' is treated as not configured."""
    from app.models.component import PlatformConfig

    cfg = PlatformConfig(key="ingest_bucket_name", value="null")
    session.add(cfg)
    await session.flush()
    await session.commit()

    resp = await client.post(
        "/api/files/upload/initiate",
        json={"filename": "sample.fastq.gz", "expected_size_bytes": 1_000_000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
