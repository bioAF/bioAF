"""Response jobs handed to the application owner (plan 14, delivery D3).

Hosted reviews, glossary scans, and lab-document URL imports used to run as response background
tasks inside the request, so a long job held the server's request drain open at shutdown. Now the
route reserves admission before committing the job, commits, then starts the job on the
application's owner and returns at once with the same response. A shutting-down owner refuses the
request before anything is written; a request that fails before starting its job releases the
reservation. Each accepted job runs exactly once.
"""

import asyncio

import pytest

import app.main as main_module
from app import runtime
from app.runtime import OwnerClosed, TaskOwner

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner():
    owner = TaskOwner()
    previous = getattr(main_module.app.state, "runtime", None)
    main_module.app.state.runtime = owner
    yield owner
    main_module.app.state.runtime = previous


@pytest.fixture
def blocking_scan(monkeypatch):
    from app.services import lab_glossary_scan_service as scan_svc

    calls: list[int] = []
    release = asyncio.Event()

    async def execute(factory, *, job_id):
        calls.append(job_id)
        await release.wait()

    monkeypatch.setattr(scan_svc, "execute_scan", execute)
    return calls, release


async def _start_scan(client, token):
    return await client.post(
        "/api/lab-knowledge/glossary/scan",
        json={"scan_type": "platform_wide"},
        headers=_auth(token),
    )


async def test_the_scan_is_started_on_the_owner_and_the_response_does_not_wait(
    client, admin_token, owner, blocking_scan
):
    calls, release = blocking_scan
    resp = await asyncio.wait_for(_start_scan(client, admin_token), 5)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    await asyncio.sleep(0)
    assert owner.roles() == ["glossary-scan"]
    assert calls == [resp.json()["id"]]
    release.set()
    assert await owner.drain(1)
    assert calls == [resp.json()["id"]]


async def test_a_shutting_down_owner_refuses_the_request_before_writing(client, admin_token, owner, blocking_scan):
    from sqlalchemy import func, select

    from app.models.lab_glossary import LabGlossaryScanJob

    calls, _ = blocking_scan
    owner.close_admission()
    resp = await _start_scan(client, admin_token)
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After")
    assert calls == []
    import app.database as database_module

    async with database_module.async_session_factory() as s:
        assert (await s.execute(select(func.count(LabGlossaryScanJob.id)))).scalar() == 0


async def test_a_reservation_released_without_starting_runs_nothing():
    owner = TaskOwner()
    with runtime.bound(owner):
        with pytest.raises(RuntimeError):
            with runtime.admission(role="glossary-scan"):
                raise RuntimeError("commit failed")
    assert owner.roles() == []
    assert await owner.drain(0.1)


async def test_a_reservation_taken_before_shutdown_may_still_start_its_job():
    owner = TaskOwner()
    ran = asyncio.Event()

    async def job():
        ran.set()

    with runtime.bound(owner):
        with runtime.admission(role="glossary-scan") as ticket:
            owner.close_admission()
            ticket.start(job())
    await asyncio.wait_for(ran.wait(), 1)


async def test_the_drain_waits_for_an_open_reservation():
    owner = TaskOwner()
    ran = asyncio.Event()

    async def job():
        ran.set()

    async def request():
        with runtime.bound(owner):
            with runtime.admission(role="glossary-scan") as ticket:
                await asyncio.sleep(0.05)  # the commit
                ticket.start(job())

    handler = asyncio.create_task(request())
    await asyncio.sleep(0.01)
    report = await owner.shutdown(drain_seconds=1, cancel_seconds=1)
    await handler
    assert ran.is_set() and report.clean


async def test_admission_without_an_owner_starts_the_job_unowned():
    ran = asyncio.Event()

    async def job():
        ran.set()

    with runtime.admission(role="glossary-scan") as ticket:
        ticket.start(job())
    await asyncio.wait_for(ran.wait(), 1)


async def test_a_closed_owner_refuses_admission():
    owner = TaskOwner()
    owner.close_admission()
    with runtime.bound(owner):
        with pytest.raises(OwnerClosed):
            with runtime.admission(role="glossary-scan"):
                pytest.fail("admitted")
