"""ADR-048: read-only file metadata endpoints. gcs_uri is excluded; bytes
do not flow through the public API in v1."""

import pytest
from sqlalchemy import text


async def _make_experiment(client, headers):
    pr = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "P", "external_id": "P-A"},
    )
    pid = pr.json()["id"]
    er = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={"name": "E", "project_id": pid, "external_id": "E-A"},
    )
    return er.json(), pid


async def _insert_file(
    session,
    org_id: int,
    *,
    project_id: int | None = None,
    experiment_id: int | None = None,
    filename: str = "x.fastq.gz",
    source_type: str = "upload",
) -> int:
    result = await session.execute(
        text(
            "INSERT INTO files (organization_id, gcs_uri, filename, file_type, source_type, "
            "project_id, experiment_id, tags_json) "
            "VALUES (:o, :u, :f, 'fastq', :s, :p, :e, '[]'::jsonb) RETURNING id"
        ),
        {
            "o": org_id,
            "u": f"gs://bucket/{filename}",
            "f": filename,
            "s": source_type,
            "p": project_id,
            "e": experiment_id,
        },
    )
    fid = result.scalar()
    await session.commit()
    return fid


@pytest.mark.asyncio
async def test_get_file_excludes_gcs_uri(client, integration_api_key, session, admin_user):
    headers = integration_api_key["headers"]
    exp, pid = await _make_experiment(client, headers)
    fid = await _insert_file(session, admin_user.organization_id, experiment_id=exp["id"], project_id=pid)
    r = await client.get(f"/api/v1/integrations/files/{fid}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "gcs_uri" not in body
    assert body["filename"] == "x.fastq.gz"
    assert body["experiment_id"] == exp["id"]
    assert body["project_id"] == pid


@pytest.mark.asyncio
async def test_list_files_filters(client, integration_api_key, session, admin_user):
    headers = integration_api_key["headers"]
    exp, pid = await _make_experiment(client, headers)
    other_pid = (
        await client.post(
            "/api/v1/integrations/projects",
            headers=headers,
            json={"name": "Other", "external_id": "OTHER"},
        )
    ).json()["id"]
    await _insert_file(session, admin_user.organization_id, experiment_id=exp["id"], project_id=pid, filename="a.fastq")
    await _insert_file(session, admin_user.organization_id, project_id=other_pid, filename="b.txt")
    r = await client.get(f"/api/v1/integrations/files?project_id={pid}", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["filename"] == "a.fastq"


@pytest.mark.asyncio
async def test_cross_org_file_returns_404(client, integration_api_key, session):
    headers = integration_api_key["headers"]
    # Create a second org's file directly. Use a fake org_id (999) which the
    # caller cannot see.
    from app.models.organization import Organization

    org = Organization(name="Other", setup_complete=True)
    session.add(org)
    await session.flush()
    other_org_id = org.id
    fid = await _insert_file(session, other_org_id)
    r = await client.get(f"/api/v1/integrations/files/{fid}", headers=headers)
    assert r.status_code == 404
