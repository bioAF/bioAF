import pytest
from app.models.file import File


@pytest.mark.asyncio
async def test_file_stats_empty(client, admin_token):
    """Stats endpoint returns empty breakdown with no files."""
    response = await client.get(
        "/api/files/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["artifacts"] == {"total": 0, "by_type": {}}
    assert data["uploaded"] == {"total": 0, "by_type": {}}


@pytest.mark.asyncio
async def test_file_stats_with_data(client, admin_token, admin_user, session):
    """Stats endpoint groups files by source_type and file_type."""
    org_id = admin_user.organization_id

    def _f(name, ftype, source):
        return File(
            filename=name,
            file_type=ftype,
            source_type=source,
            organization_id=org_id,
            gcs_uri=f"gs://test-bucket/{name}",
        )

    files = [
        _f("report.pdf", "pdf", "pipeline"),
        _f("plot.png", "png", "pipeline"),
        _f("plot2.png", "png", "pipeline"),
        _f("matrix.h5ad", "h5ad", "pipeline"),
        _f("reads.fastq.gz", "fastq", "upload"),
        _f("reads2.fastq.gz", "fastq", "upload"),
    ]
    for f in files:
        session.add(f)
    await session.commit()

    response = await client.get(
        "/api/files/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["artifacts"]["total"] == 4
    assert data["artifacts"]["by_type"]["pdf"] == 1
    assert data["artifacts"]["by_type"]["png"] == 2
    assert data["artifacts"]["by_type"]["h5ad"] == 1

    assert data["uploaded"]["total"] == 2
    assert data["uploaded"]["by_type"]["fastq"] == 2
