"""Stage 3a: production read paths must source the object-store URI from the
neutral ``storage_uri`` column, not the legacy ``gcs_uri`` column.

During the expand/contract transition BOTH columns exist and the ORM sync shim
(``app.models._storage_uri_sync``) keeps them equal on every write, so a
black-box test of two equal columns cannot tell which one a reader uses. Each
test here DIVERGES the columns with a raw ``UPDATE`` (which bypasses the ORM
shim, since core SQL fires no ``before_update`` event) and asserts the reader
returns the ``storage_uri`` value. A test goes red while a path still reads
``gcs_uri`` and green once that path is repointed at ``storage_uri``.

The wire/API field stays named ``gcs_uri`` (the frontend contract is unchanged
this stage); only its SOURCE moves to the neutral column. The legacy ``gcs_uri``
column is kept and the sync shim left in place; dropping it is deferred to the
end-of-project cleanup sweep.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text


def test_file_response_exposes_storage_uri_alias():
    """The FileResponse serializes a neutral storage_uri mirroring gcs_uri (DB-free)."""
    from app.schemas.file import FileResponse

    r = FileResponse(
        id=1,
        filename="x.bam",
        gcs_uri="gs://bucket/x.bam",
        size_bytes=None,
        md5_checksum=None,
        file_type="bam",
        upload_timestamp=datetime(2020, 1, 1),
        created_at=datetime(2020, 1, 1),
    )
    dumped = r.model_dump()
    assert dumped["gcs_uri"] == "gs://bucket/x.bam"
    assert dumped["storage_uri"] == "gs://bucket/x.bam"


async def _diverge_file_storage_uri(session, file_id: int, storage_uri: str) -> None:
    """Point a ``files`` row's storage_uri at a value different from its gcs_uri,
    bypassing the ORM sync shim (raw SQL fires no before_update event)."""
    await session.execute(
        text("UPDATE files SET storage_uri = :s WHERE id = :fid").bindparams(s=storage_uri, fid=file_id)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_file_response_sources_uri_from_storage_uri(client, admin_token, sample_file, session):
    """GET /api/files/{id} returns the storage_uri value in its gcs_uri field."""
    neutral = "gs://neutral-bucket/from-storage-uri.fastq.gz"
    await _diverge_file_storage_uri(session, sample_file.id, neutral)

    resp = await client.get(
        f"/api/files/{sample_file.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    # Both the legacy wire key and the neutral one carry the storage_uri value.
    assert resp.json()["gcs_uri"] == neutral
    assert resp.json()["storage_uri"] == neutral


@pytest.mark.asyncio
async def test_file_download_signs_storage_uri(client, admin_token, sample_file, session, monkeypatch):
    """The signed-download URL is minted for the storage_uri, not gcs_uri."""
    neutral = "gs://neutral-bucket/from-storage-uri.fastq.gz"
    await _diverge_file_storage_uri(session, sample_file.id, neutral)

    adapter = MagicMock()
    adapter.generate_signed_url = AsyncMock(side_effect=lambda uri, **_: f"https://signed/{uri}")
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)

    resp = await client.get(
        f"/api/files/{sample_file.id}/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["download_url"] == f"https://signed/{neutral}"
    assert adapter.generate_signed_url.await_args.args[0] == neutral
