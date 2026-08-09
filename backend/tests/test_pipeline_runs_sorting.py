"""Sorting a paginated list must sort the list, not the page.

Measured on the demo before this existed: 29 runs, page size 25, backend
ordering created_at DESC. Clicking "ID" ascending in the UI put run **#5** at
the top, because the client sorted the 25 rows it already had. The lowest ID in
the list was **#1**, on page 2. The user asked for the smallest and was shown
the smallest of an arbitrary subset -- an answer that is wrong, not merely
partial, and indistinguishable from the right one.

Fixing it in the client is not possible: page 1 of 40 does not contain the
answer. So the ordering moves to the query, behind an allowlist, because a sort
field is a column name and must never be taken from user input unchecked.
"""

import pytest
import pytest_asyncio

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Sorting Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def three_runs(session, admin_user, experiment):
    """Three runs whose ID order and created_at order disagree.

    Inserted OLDEST first, so IDs ascend while created_at descends. Without
    that disagreement an ascending-ID assertion passes on the default ordering
    alone and proves nothing: the first draft of this fixture did exactly that.
    Pipeline names are also deliberately not in ID order (zulu, alpha, mike).
    """
    from datetime import datetime, timedelta, timezone

    from app.models.pipeline_run import PipelineRun

    base = datetime.now(timezone.utc)
    runs = []
    entries = [("zulu", "completed"), ("alpha", "failed"), ("mike", "running")]
    for offset, (name, status) in enumerate(entries):
        age = len(entries) - offset  # first inserted is the oldest
        run = PipelineRun(
            organization_id=admin_user.organization_id,
            experiment_id=experiment.id,
            pipeline_name=name,
            pipeline_version="1.0.0",
            status=status,
            submitted_by_user_id=admin_user.id,
            created_at=base - timedelta(hours=age),
        )
        session.add(run)
        await session.flush()
        runs.append(run)
    await session.commit()
    return runs


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_default_order_is_unchanged(client, admin_token, three_runs):
    """No sort parameters means exactly what it meant before: newest first."""
    r = await client.get("/api/pipeline-runs?page_size=100", headers=_auth(admin_token))
    assert r.status_code == 200
    created = [run["created_at"] for run in r.json()["runs"]]
    assert created == sorted(created, reverse=True)


@pytest.mark.asyncio
async def test_sort_by_id_ascending(client, admin_token, three_runs):
    r = await client.get(
        "/api/pipeline-runs?sort_by=id&sort_dir=asc&page_size=100", headers=_auth(admin_token)
    )
    assert r.status_code == 200
    ids = [run["id"] for run in r.json()["runs"]]
    assert ids == sorted(ids)


@pytest.mark.asyncio
async def test_sort_reaches_past_the_first_page(client, admin_token, three_runs):
    """The whole point. Page 1 of an ascending sort must hold the true minimum.

    With page_size=1 the client-side approach cannot be right by accident:
    one row is already "sorted", so only a sort applied before the limit can
    return the real first item.
    """
    everything = await client.get("/api/pipeline-runs?page_size=100", headers=_auth(admin_token))
    all_ids = [run["id"] for run in everything.json()["runs"]]

    first_page = await client.get(
        "/api/pipeline-runs?sort_by=id&sort_dir=asc&page=1&page_size=1", headers=_auth(admin_token)
    )
    assert first_page.status_code == 200
    assert [run["id"] for run in first_page.json()["runs"]] == [min(all_ids)]

    last = await client.get(
        "/api/pipeline-runs?sort_by=id&sort_dir=desc&page=1&page_size=1", headers=_auth(admin_token)
    )
    assert [run["id"] for run in last.json()["runs"]] == [max(all_ids)]


@pytest.mark.asyncio
async def test_sort_by_status_and_pipeline_name(client, admin_token, three_runs):
    """The other two columns the UI offers as sortable."""
    for field in ("status", "pipeline_name"):
        r = await client.get(
            f"/api/pipeline-runs?sort_by={field}&sort_dir=asc&page_size=100",
            headers=_auth(admin_token),
        )
        assert r.status_code == 200, field
        values = [run[field] for run in r.json()["runs"]]
        assert values == sorted(values), field


@pytest.mark.asyncio
async def test_unknown_sort_field_is_refused(client, admin_token, three_runs):
    """A sort field is a column name. It is never taken from input unchecked."""
    for bad in ("password_hash", "organization_id", "id; DROP TABLE pipeline_runs", ""):
        r = await client.get(
            f"/api/pipeline-runs?sort_by={bad}", headers=_auth(admin_token)
        )
        assert r.status_code == 422, f"{bad!r} was not refused"


@pytest.mark.asyncio
async def test_unknown_sort_direction_is_refused(client, admin_token, three_runs):
    r = await client.get(
        "/api/pipeline-runs?sort_by=id&sort_dir=sideways", headers=_auth(admin_token)
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_sorting_does_not_leak_across_organizations(
    client, admin_token, three_runs, session, admin_user
):
    """Sorting must not become a way to read another org's rows.

    The ordering is applied to a query that is already scoped by
    organization_id; this pins that the scope survives the new clause.
    """
    from app.models.organization import Organization
    from app.models.pipeline_run import PipelineRun

    other = Organization(name="Other Org")
    session.add(other)
    await session.flush()
    intruder = PipelineRun(
        organization_id=other.id,
        pipeline_name="aaa-should-never-be-listed",
        pipeline_version="1.0.0",
        status="completed",
        submitted_by_user_id=admin_user.id,
    )
    session.add(intruder)
    await session.commit()

    r = await client.get(
        "/api/pipeline-runs?sort_by=pipeline_name&sort_dir=asc&page_size=100",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    names = [run["pipeline_name"] for run in r.json()["runs"]]
    assert "aaa-should-never-be-listed" not in names


@pytest.mark.asyncio
async def test_page_size_is_bounded(client, admin_token, three_runs):
    """A page-size selector is user input reaching a LIMIT.

    The UI offers 25/50/100; the endpoint must refuse an unbounded read rather
    than trusting the control to be the only caller.
    """
    ok = await client.get("/api/pipeline-runs?page_size=100", headers=_auth(admin_token))
    assert ok.status_code == 200

    too_big = await client.get("/api/pipeline-runs?page_size=100000", headers=_auth(admin_token))
    assert too_big.status_code == 422

    too_small = await client.get("/api/pipeline-runs?page_size=0", headers=_auth(admin_token))
    assert too_small.status_code == 422
