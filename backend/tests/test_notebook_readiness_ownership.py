"""Readiness publishes "running" and an access URL only for a workload verified to be the session's.

The same rules as Stop and cleanup: the recorded identity and cluster (``verify_workload``), then
the live pod's and Service's labels, organization included. A mismatch anywhere is not ready: no
running transition and no URL, on both the periodic pass and the session list. A correctly owned
notebook still becomes usable on both.
"""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod, notebook_service
from tests.test_notebook_lifecycle_state import CLUSTER, T0, _k8s, _pass, _row, _seed

URL = "http://203.0.113.9:8888"


@pytest.fixture
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


def _labelled(obj, org):
    obj.metadata.labels = {**obj.metadata.labels, "bioaf.io/organization": str(org)}
    return obj


async def _observe(path, session, factory, adapter, monkeypatch, ns):
    if path == "pass":
        await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=13))
    else:
        from datetime import datetime, timezone

        from app.api.notebook_sessions import _sync_session_from_k8s
        from app.services import notebook_lifecycle

        # The list path uses the real clock: give the launch a current start, inside its deadline.
        recent = datetime.now(timezone.utc) - timedelta(minutes=13)
        lifecycle = {**ns.provider_metadata["lifecycle"], "reserved_at": recent.isoformat()}
        ns.provider_metadata = {**ns.provider_metadata, "lifecycle": lifecycle}
        await session.commit()

        monkeypatch.setattr(notebook_lifecycle, "get_notebook_adapter", lambda: adapter)
        await _sync_session_from_k8s(ns, session)
        await session.commit()


def _owned_core(ns, *, pod_org=None, svc_org=None):
    pod = notebook_pod(ns.id, ns.user_id)
    svc = notebook_service(ns.id)
    if pod_org is not None:
        _labelled(pod, pod_org)
    if svc_org is not None:
        _labelled(svc, svc_org)
    return FakeCore(pods=[pod], services=[svc])


@pytest.mark.parametrize("path", ["pass", "list"])
@pytest.mark.parametrize("mismatch", ["cluster", "pod", "service"])
@pytest.mark.asyncio
async def test_a_mismatch_publishes_neither_running_nor_a_url(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, path, mismatch
):
    ns = await _seed(session, admin_user)
    org, other = admin_user.organization_id, admin_user.organization_id + 1
    core = _owned_core(ns, pod_org=other if mismatch == "pod" else org, svc_org=other if mismatch == "service" else org)
    adapter = _k8s(mode_adapter_factory, monkeypatch, core, url=URL)
    if mismatch == "cluster":

        async def other_cluster():
            return "another-project/us-west1/another-cluster"

        monkeypatch.setattr(adapter, "workload_cluster", other_cluster)

    await _observe(path, session, factory, adapter, monkeypatch, ns)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("starting", None)


@pytest.mark.parametrize("path", ["pass", "list"])
@pytest.mark.parametrize("labelled", [True, False], ids=["organization-labelled", "legacy-unlabelled"])
@pytest.mark.asyncio
async def test_a_correctly_owned_notebook_still_becomes_usable(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, path, labelled
):
    ns = await _seed(session, admin_user)
    org = admin_user.organization_id if labelled else None
    adapter = _k8s(mode_adapter_factory, monkeypatch, _owned_core(ns, pod_org=org, svc_org=org), url=URL)

    await _observe(path, session, factory, adapter, monkeypatch, ns)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("running", URL)


@pytest.mark.parametrize("path", ["pass", "list"])
@pytest.mark.asyncio
async def test_a_running_session_never_gains_a_url_from_someone_elses_service(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, path
):
    ns = await _seed(session, admin_user, status="running")
    core = _owned_core(ns, pod_org=admin_user.organization_id, svc_org=admin_user.organization_id + 1)
    adapter = _k8s(mode_adapter_factory, monkeypatch, core, url=URL)

    if path == "pass":
        # The pass observes only starting launches; a running session is refreshed by the list.
        from app.services import notebook_lifecycle

        monkeypatch.setattr(notebook_lifecycle, "get_notebook_adapter", lambda: adapter)
        await notebook_lifecycle.observe_session(session, ns, now=T0 + timedelta(minutes=13))
        await session.commit()
    else:
        await _observe(path, session, factory, adapter, monkeypatch, ns)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("running", None)


@pytest.mark.asyncio
async def test_an_unverifiable_launch_still_meets_its_deadline(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    """Ownership uncertainty is not progress forever: the 30-minute deadline still ends it."""
    ns = await _seed(session, admin_user)
    core = _owned_core(ns, pod_org=admin_user.organization_id + 1, svc_org=admin_user.organization_id + 1)
    adapter = _k8s(mode_adapter_factory, monkeypatch, core, url=URL)

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=31))

    row = await _row(factory, ns.id)
    assert row.status == "failed" and row.access_url is None
    assert CLUSTER  # the launch's recorded cluster matched; only the live labels disagreed


@pytest.mark.asyncio
async def test_ownership_and_the_address_come_from_one_service_read(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    """No second read can bring in a replacement Service's address: there is only one read."""
    ns = await _seed(session, admin_user)
    org = admin_user.organization_id
    core = _owned_core(ns, pod_org=org, svc_org=org)
    adapter = _k8s(mode_adapter_factory, monkeypatch, core, url=URL)

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=13))

    assert (await _row(factory, ns.id)).access_url == URL
    assert adapter.service_reads == [f"bioaf-notebook-svc-{ns.id}"]
    assert [c for c in core.calls if c[:2] == ("read", "service")] == []


@pytest.mark.asyncio
async def test_a_foreign_service_with_a_usable_address_publishes_nothing_through_the_real_resolver(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    """The real resolver, a mocked API response: a foreign Service carrying an address is never used."""
    from tests.support.notebook_k8s_fakes import notebook_service as svc_factory

    ns = await _seed(session, admin_user)
    org = admin_user.organization_id
    core = _owned_core(ns, pod_org=org, svc_org=org)
    adapter = _k8s(mode_adapter_factory, monkeypatch, core, url=URL)
    replacement = _labelled(svc_factory(ns.id, uid="svc-uid-2"), org + 1)
    core.services[replacement.metadata.name] = replacement

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=13))

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("starting", None)
