"""Which files an export and its estimate carry, and where each one goes (plan 24 decision 2)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.data_export import selection


async def _base(session, org: int) -> None:
    await session.execute(
        text("INSERT INTO projects (id, organization_id, name, status) VALUES (1, :org, 'P', 'active')"), {"org": org}
    )
    # Experiment uuids fix the "lowest associated experiment" order: B (id 2) sorts before A (id 1).
    await session.execute(
        text(
            "INSERT INTO experiments (id, uuid, organization_id, project_id, name, status) VALUES "
            "(1, 'bbbbbbbb-0000-0000-0000-000000000001', :org, 1, 'A', 'registered'), "
            "(2, 'aaaaaaaa-0000-0000-0000-000000000002', :org, 1, 'B', 'registered')"
        ),
        {"org": org},
    )
    await session.execute(
        text(
            "INSERT INTO samples (id, experiment_id, external_id, status) VALUES "
            "(11, 1, 'A1', 'registered'), (21, 2, 'B1', 'registered')"
        )
    )


async def _file(
    session,
    org: int,
    fid: int,
    *,
    experiment_id: int | None = None,
    project_id: int | None = None,
    file_type: str = "csv",
    size: int | None = 10,
    deleted: bool = False,
    is_global: bool = False,
    samples: tuple[int, ...] = (),
) -> None:
    await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, storage_uri, file_type, size_bytes, "
            "experiment_id, project_id, source_type, deleted_at, is_global) VALUES (:id, :org, :name, :uri, :uri, "
            ":ftype, :size, :eid, :pid, 'upload', CASE WHEN :deleted THEN now() END, :glob)"
        ),
        {
            "id": fid,
            "org": org,
            "name": f"f{fid}.{file_type}",
            "uri": f"gs://b/f{fid}",
            "ftype": file_type,
            "size": size,
            "eid": experiment_id,
            "pid": project_id,
            "deleted": deleted,
            "glob": is_global,
        },
    )
    for sid in samples:
        await session.execute(
            text("INSERT INTO sample_files (sample_id, file_id) VALUES (:s, :f)"), {"s": sid, "f": fid}
        )


@pytest_asyncio.fixture
async def seeded(session, admin_user):
    org = admin_user.organization_id
    await _base(session, org)
    await _file(session, org, 101, experiment_id=1)  # direct to A
    await _file(session, org, 102, samples=(11,))  # sample-linked to A only
    await _file(session, org, 103, experiment_id=1, samples=(21,))  # direct A, also linked to B
    await _file(session, org, 104, samples=(11, 21))  # linked to A and B; B has the lower uuid
    await _file(session, org, 105, project_id=1)  # project-only
    await _file(session, org, 106, experiment_id=1, deleted=True)  # deleted from view
    await _file(session, org, 107, is_global=True)  # global, unrelated
    await _file(session, org, 108, experiment_id=1, file_type="fastq.gz", size=1000)
    await session.commit()
    return org


def _ids(files) -> list[int]:
    return [f.id for f in files]


@pytest.mark.asyncio
async def test_an_experiment_carries_direct_and_sample_linked_files_once(session, seeded):
    content = await selection.select_experiment(session, 1, seeded, include_fastq=False)

    assert sorted(_ids(content.files)) == [101, 102, 103, 104]


@pytest.mark.asyncio
async def test_an_experiment_with_fastq_carries_it_under_raw(session, seeded):
    content = await selection.select_experiment(session, 1, seeded, include_fastq=True)

    fastq = [f for f in content.files if f.id == 108]
    assert [f.folder for f in fastq] == ["raw"]


@pytest.mark.asyncio
async def test_a_project_assigns_each_file_once(session, seeded):
    content = await selection.select_project(session, 1, seeded, include_fastq=False)

    by_experiment = {e.name: sorted(_ids(e.files)) for e in content.experiments}
    assert by_experiment == {"A": [101, 102, 103], "B": [104]}
    assert _ids(content.files) == [105]
    assert sorted(_ids(content.all_files)) == [101, 102, 103, 104, 105]


@pytest.mark.asyncio
async def test_selection_order_is_deterministic(session, seeded):
    first = await selection.select_project(session, 1, seeded, include_fastq=True)
    second = await selection.select_project(session, 1, seeded, include_fastq=True)

    assert [f.uuid for f in first.all_files] == [f.uuid for f in second.all_files]
    assert [e.uuid for e in first.experiments] == sorted(e.uuid for e in first.experiments)


@pytest.mark.asyncio
async def test_a_foreign_project_selects_nothing(session, seeded):
    assert await selection.select_project(session, 1, seeded + 999, include_fastq=False) is None
    assert await selection.select_experiment(session, 1, seeded + 999, include_fastq=False) is None


# -- Estimates follow the same assignment ---------------------------------------------------------


@pytest.mark.asyncio
async def test_experiment_estimate_counts_sample_linked_and_excludes_deleted(client, seeded, admin_token):
    resp = await client.get("/api/experiments/1/export/estimate", headers={"Authorization": f"Bearer {admin_token}"})

    assert resp.status_code == 200
    assert resp.json() == {"total_bytes": 40, "breakdown": {"csv": 40}}


@pytest.mark.asyncio
async def test_project_estimate_uses_the_export_assignment_with_no_fictitious_row(client, seeded, admin_token):
    resp = await client.get(
        "/api/projects/1/export/estimate?include_fastq=true", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_bytes"] == 1050
    assert body["breakdown"] == {"csv": 50, "fastq.gz": 1000}
    rows = {r["name"]: (r["experiment_id"], r["total_bytes"]) for r in body["experiments"]}
    assert rows == {"A": (1, 1030), "B": (2, 10)}


@pytest.mark.asyncio
async def test_an_unknown_size_counts_as_zero_in_the_estimate(client, session, admin_user, admin_token):
    org = admin_user.organization_id
    await _base(session, org)
    await _file(session, org, 201, experiment_id=1, size=None)
    await session.commit()

    resp = await client.get("/api/experiments/1/export/estimate", headers={"Authorization": f"Bearer {admin_token}"})

    assert resp.json()["total_bytes"] == 0
