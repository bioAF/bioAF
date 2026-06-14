"""storage_uri is the authoritative backend-neutral column; gcs_uri is a mirror.

AWS-prep. storage_uri is the canonical physical object-store URI column. gcs_uri
is a RETAINED legacy mirror kept in sync (it stays NOT NULL) so an operator can
confirm nothing depends on it and drop it later; we do NOT drop it here. The ORM
mirrors them on every write (app.models._storage_uri_sync), with storage_uri
canonical: app code writes storage_uri and gcs_uri follows.
"""

import pytest
from sqlalchemy import select

from app.models.file import File


@pytest.mark.asyncio
async def test_write_neutral_name_mirrors_to_gcs_uri(session, admin_user):
    # The primary path: app code writes the canonical storage_uri; gcs_uri mirrors.
    f = File(
        organization_id=admin_user.organization_id,
        storage_uri="gs://bioaf-raw/a/b.bam",
        filename="b.bam",
        file_type="bam",
        source_type="upload",
    )
    session.add(f)
    await session.flush()

    assert f.storage_uri == "gs://bioaf-raw/a/b.bam"
    assert f.gcs_uri == "gs://bioaf-raw/a/b.bam"

    # Both columns are real and query the same value.
    by_neutral = (await session.execute(select(File).where(File.storage_uri == "gs://bioaf-raw/a/b.bam"))).scalar_one()
    by_legacy = (await session.execute(select(File).where(File.gcs_uri == "gs://bioaf-raw/a/b.bam"))).scalar_one()
    assert by_neutral.id == by_legacy.id == f.id


@pytest.mark.asyncio
async def test_write_legacy_name_backfills_storage_uri(session, admin_user):
    # A legacy-style write that sets only gcs_uri is still mirrored INTO
    # storage_uri so readers (which read storage_uri) stay correct.
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-raw/x/y.h5ad",
        filename="y.h5ad",
        file_type="h5ad",
        source_type="upload",
    )
    session.add(f)
    await session.flush()
    assert f.storage_uri == "gs://bioaf-raw/x/y.h5ad"
    assert f.gcs_uri == "gs://bioaf-raw/x/y.h5ad"


@pytest.mark.asyncio
async def test_orm_update_to_storage_uri_resyncs_gcs_uri(session, admin_user):
    f = File(
        organization_id=admin_user.organization_id,
        storage_uri="gs://bioaf-ingest/old.fastq.gz",
        filename="old.fastq.gz",
        file_type="fastq",
        source_type="upload",
    )
    session.add(f)
    await session.flush()

    f.storage_uri = "gs://bioaf-raw/new.fastq.gz"  # ORM update (e.g. a file move)
    await session.flush()
    assert f.gcs_uri == "gs://bioaf-raw/new.fastq.gz"
