"""storage_uri is the honest column name; gcs_uri stays as a synonym (BAL Phase 4).

The opaque object-store URI column was named gcs_uri, which is dishonest once a
non-GCS backend (S3/NFS) is in play. It is renamed to storage_uri, with a
SQLAlchemy synonym so existing code (and API responses) that use `gcs_uri` keep
resolving during the transition. Both names must read/write/query the same
column.
"""

import pytest
from sqlalchemy import select

from app.models.file import File


@pytest.mark.asyncio
async def test_both_names_resolve_on_a_persisted_file(session, admin_user):
    # Construct via the legacy name; read via the new name.
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

    # Both attributes are usable as query expressions over the same column.
    by_legacy = (await session.execute(select(File).where(File.gcs_uri == "gs://bioaf-raw/x/y.h5ad"))).scalar_one()
    by_neutral = (await session.execute(select(File).where(File.storage_uri == "gs://bioaf-raw/x/y.h5ad"))).scalar_one()
    assert by_legacy.id == by_neutral.id == f.id


@pytest.mark.asyncio
async def test_write_via_neutral_name_reads_via_legacy(session, admin_user):
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
