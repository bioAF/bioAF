"""storage_uri is a real backend-neutral column kept in sync with gcs_uri.

BAL rework, Phase 4 (expand/contract). storage_uri is being introduced as the
physical column to eventually replace the GCS-presuming gcs_uri. During the
transition BOTH are real columns and must hold the same value (so live installs
and external readers of gcs_uri keep working until a later migration drops it).
The ORM mirrors them on every write (app.models._storage_uri_sync).
"""

import pytest
from sqlalchemy import select

from app.models.file import File


@pytest.mark.asyncio
async def test_write_legacy_name_mirrors_to_storage_uri(session, admin_user):
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-raw/x/y.h5ad",
        filename="y.h5ad",
        file_type="h5ad",
        source_type="upload",
    )
    session.add(f)
    await session.flush()

    assert f.gcs_uri == "gs://bioaf-raw/x/y.h5ad"
    assert f.storage_uri == "gs://bioaf-raw/x/y.h5ad"

    # Both columns are real and query the same value.
    by_legacy = (await session.execute(select(File).where(File.gcs_uri == "gs://bioaf-raw/x/y.h5ad"))).scalar_one()
    by_neutral = (await session.execute(select(File).where(File.storage_uri == "gs://bioaf-raw/x/y.h5ad"))).scalar_one()
    assert by_legacy.id == by_neutral.id == f.id


@pytest.mark.asyncio
async def test_write_neutral_name_backfills_gcs_uri(session, admin_user):
    # New-style write that only sets storage_uri must still populate gcs_uri
    # (which is NOT NULL during the transition).
    f = File(
        organization_id=admin_user.organization_id,
        storage_uri="gs://bioaf-raw/a/b.bam",
        filename="b.bam",
        file_type="bam",
        source_type="upload",
    )
    session.add(f)
    await session.flush()
    assert f.gcs_uri == "gs://bioaf-raw/a/b.bam"
    assert f.storage_uri == "gs://bioaf-raw/a/b.bam"


@pytest.mark.asyncio
async def test_orm_update_to_gcs_uri_resyncs_storage_uri(session, admin_user):
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-ingest/old.fastq.gz",
        filename="old.fastq.gz",
        file_type="fastq",
        source_type="upload",
    )
    session.add(f)
    await session.flush()

    f.gcs_uri = "gs://bioaf-raw/new.fastq.gz"  # ORM update (e.g. a file move)
    await session.flush()
    assert f.storage_uri == "gs://bioaf-raw/new.fastq.gz"
