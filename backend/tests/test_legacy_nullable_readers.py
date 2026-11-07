"""Readers of columns every released schema allows to be NULL (plan 9, section 1).

The models now accept these NULLs (test_migrations_apply.LEGACY_NULLABLE). A NULL in one row must
not cost the caller the rows that are complete.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.notebook_sessions import _session_response
from app.models.file import File
from app.models.notebook_session import ComputeSession


def _compute_session(accessed_files):
    return SimpleNamespace(
        id=1,
        session_type="jupyter",
        user=None,
        experiment=None,
        project=None,
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        requested_disk_gb=None,
        status="stopped",
        failure_reason=None,
        failure_message=None,
        idle_since=None,
        access_url=None,
        proxy_url=None,
        started_at=None,
        stopped_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        git_branch_name=None,
        git_commit_hash=None,
        environment_version_id=None,
        provider_metadata=None,
        accessed_files=accessed_files,
    )


def _link(file_id, access_type):
    return SimpleNamespace(file_id=file_id, access_type=access_type)


def test_recreate_keeps_the_complete_inputs_when_a_legacy_link_has_no_file():
    response = _session_response(
        _compute_session([_link(7, "input"), _link(None, "input"), _link(3, "input"), _link(9, "output")])
    )
    assert response.input_file_ids == [3, 7]


def test_a_link_with_no_recorded_direction_is_not_offered_as_an_input():
    response = _session_response(_compute_session([_link(7, "input"), _link(8, None)]))
    assert response.input_file_ids == [7]


@pytest.mark.asyncio
async def test_provenance_does_not_call_a_link_with_no_recorded_direction_an_output(
    client, session, admin_user, admin_token
):
    """Owner decision (2026-11-07): a link whose direction was never recorded is in neither list."""
    compute = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status="stopped",
    )
    files = [
        File(
            organization_id=admin_user.organization_id,
            filename=name,
            storage_uri=f"gs://b/{name}",
            file_type=name.rsplit(".", 1)[1],
            source_type="upload",
        )
        for name in ("in.h5ad", "out.png", "unknown.csv")
    ]
    session.add_all([compute, *files])
    await session.flush()
    for f, direction in zip(files, ("input", "output", None)):
        await session.execute(
            text("INSERT INTO notebook_session_files (session_id, file_id, access_type) VALUES (:s, :f, :a)"),
            {"s": compute.id, "f": f.id, "a": direction},
        )
    await session.commit()

    resp = await client.get(
        f"/api/v1/notebooks/sessions/{compute.id}/provenance", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [f["filename"] for f in body["input_files"]] == ["in.h5ad"]
    assert [f["filename"] for f in body["output_files"]] == ["out.png"]
