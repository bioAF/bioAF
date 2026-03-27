"""Tests for notebook output provenance (ADR-039)."""

import pytest
from sqlalchemy import select

from app.models.file import File
from app.models.notebook_session import ComputeSession
from app.models.notebook_session_file import NotebookSessionFile


@pytest.mark.asyncio
async def test_notebook_session_file_output(session, admin_user):
    """NotebookSessionFile with access_type='output' links a file to a session."""
    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(cs)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/output.h5ad",
        filename="output.h5ad",
        file_type="h5ad",
        source_type="notebook_output",
        source_notebook_session_id=cs.id,
    )
    session.add(f)
    await session.flush()

    nsf = NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="output")
    session.add(nsf)
    await session.flush()

    assert nsf.id is not None
    assert nsf.session_id == cs.id
    assert nsf.file_id == f.id
    assert nsf.access_type == "output"
    assert nsf.accessed_at is not None


@pytest.mark.asyncio
async def test_notebook_session_file_input(session, admin_user):
    """NotebookSessionFile with access_type='input' links a file to a session."""
    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="rstudio",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(cs)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/input.csv",
        filename="input.csv",
        file_type="csv",
    )
    session.add(f)
    await session.flush()

    nsf = NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="input")
    session.add(nsf)
    await session.flush()

    assert nsf.id is not None
    assert nsf.access_type == "input"


@pytest.mark.asyncio
async def test_notebook_session_file_unique_constraint(session, admin_user):
    """Unique constraint on (session_id, file_id, access_type) prevents duplicates."""
    from sqlalchemy.exc import IntegrityError

    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(cs)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/data.csv",
        filename="data.csv",
        file_type="csv",
    )
    session.add(f)
    await session.flush()

    nsf1 = NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="input")
    session.add(nsf1)
    await session.flush()

    nsf2 = NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="input")
    session.add(nsf2)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.asyncio
async def test_file_source_type_notebook_output_via_api(client, admin_token, session, admin_user):
    """source_type='notebook_output' is accepted and returned by the files API."""
    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(cs)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/nb-output.png",
        filename="nb-output.png",
        file_type="png",
        uploader_user_id=admin_user.id,
        source_type="notebook_output",
        source_notebook_session_id=cs.id,
    )
    session.add(f)
    await session.commit()

    resp = await client.get(f"/api/files/{f.id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "notebook_output"
    assert data["source_notebook_session_id"] == cs.id


@pytest.mark.asyncio
async def test_source_notebook_session_id_in_get_response(client, admin_token, session, admin_user):
    """source_notebook_session_id is returned in GET /api/files/{id} response."""
    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="vscode",
        resource_profile="medium",
        cpu_cores=4,
        memory_gb=8,
        status="running",
    )
    session.add(cs)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/analysis.rds",
        filename="analysis.rds",
        file_type="rds",
        uploader_user_id=admin_user.id,
        source_type="notebook_output",
        source_notebook_session_id=cs.id,
    )
    session.add(f)
    await session.commit()

    resp = await client.get(f"/api/files/{f.id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["source_notebook_session_id"] == cs.id


@pytest.mark.asyncio
async def test_provenance_downstream_notebook_sessions(session, admin_user):
    """gather_artifact() populates downstream_usage with notebook sessions that consumed the file."""
    from app.services.provenance.data_gatherer import ProvenanceDataGatherer

    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(cs)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/matrix.h5ad",
        filename="matrix.h5ad",
        file_type="h5ad",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    # Link file as input to the session
    nsf = NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="input")
    session.add(nsf)
    await session.commit()

    result = await ProvenanceDataGatherer.gather_artifact(session, f.id, admin_user.organization_id)
    # downstream_usage should include the notebook session
    notebook_entries = [d for d in result.downstream_usage if d.get("notebook_session_id")]
    assert len(notebook_entries) == 1
    assert notebook_entries[0]["notebook_session_id"] == cs.id
    assert notebook_entries[0]["session_type"] == "jupyter"


@pytest.mark.asyncio
async def test_delete_file_removes_notebook_session_files(session, admin_user):
    """Deleting a file removes its notebook_session_files rows via ORM."""
    from app.services.file_service import FileService

    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(cs)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/to-delete.csv",
        filename="to-delete.csv",
        file_type="csv",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    nsf = NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="output")
    session.add(nsf)
    await session.commit()

    # Verify row exists
    row = await session.execute(select(NotebookSessionFile).where(NotebookSessionFile.file_id == f.id))
    assert row.scalar_one_or_none() is not None

    deleted = await FileService.delete_file_record(session, f.id, admin_user.organization_id, admin_user.id)
    assert deleted is True
    await session.commit()

    # Verify notebook_session_files row was removed
    row = await session.execute(select(NotebookSessionFile).where(NotebookSessionFile.file_id == f.id))
    assert row.scalar_one_or_none() is None
