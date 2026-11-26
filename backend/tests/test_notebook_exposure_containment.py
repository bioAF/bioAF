"""Plan 03.5F (owner decision 3): contain already-exposed notebook sessions without touching their work.

Sessions launched before the gateway have a public LoadBalancer with no notebook authentication. The
containment inventories them and, only when asked, changes each bioAF-owned Service to ClusterIP: the
public listener goes away, while the Pod, its home and outputs stay. Their old Kubernetes credential
cannot be removed in place, so the report says those sessions need a save and relaunch.
"""

import copy
from types import SimpleNamespace

import pytest
from kubernetes.client import V1ObjectMeta, V1Service, V1ServicePort, V1ServiceSpec
from sqlalchemy import select

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.models.notebook_session import ComputeSession
from app.services.notebook_exposure import contain_exposed_sessions
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import NAMESPACE, FakeCore, notebook_pod


def _lb_service(session_id, *, labels=None, kind="LoadBalancer"):
    return V1Service(
        metadata=V1ObjectMeta(
            name=f"bioaf-notebook-svc-{session_id}",
            namespace=NAMESPACE,
            uid=f"svc-{session_id}",
            resource_version="7",
            labels=labels if labels is not None else {"bioaf.io/session": str(session_id)},
        ),
        spec=V1ServiceSpec(
            type=kind,
            selector={"bioaf.io/session": str(session_id)},
            ports=[V1ServicePort(port=8888, target_port=8888, protocol="TCP", node_port=31234)],
            external_traffic_policy="Cluster" if kind == "LoadBalancer" else None,
        ),
    )


class _Core(FakeCore):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.replaced = []

    def replace_namespaced_service(self, name, namespace, body, **_):
        self.calls.append(("replace", "service", name))
        self.replaced.append(copy.deepcopy(body))
        self.services[name] = body
        return body


@pytest.fixture
def adapter(mode_adapter_factory):
    import time
    from unittest.mock import MagicMock

    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    return provider


async def _session(session, user, sid, status="running", session_type="jupyter"):
    ns = ComputeSession(
        id=sid,
        user_id=user.id,
        organization_id=user.organization_id,
        session_type=session_type,
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status=status,
        compute_job_ref=f"bioaf-notebook-{sid}",
        access_url=f"http://34.1.2.{sid}:8888",
        proxy_url=f"http://34.1.2.{sid}:8888",
        provider_metadata={"pod_name": f"bioaf-notebook-{sid}", "namespace": NAMESPACE},
    )
    session.add(ns)
    await session.commit()
    return ns


def _pods(user, *sids):
    pods = []
    for sid in sids:
        pod = notebook_pod(sid, user.id)
        pod.spec.automount_service_account_token = None
        pods.append(pod)
    return pods


@pytest.mark.asyncio
async def test_the_inventory_reports_exposed_sessions_and_changes_nothing(session, comp_bio_user, adapter, monkeypatch):
    await _session(session, comp_bio_user, 42)
    core = _Core(pods=_pods(comp_bio_user, 42), services=[_lb_service(42)])
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)
    findings = await contain_exposed_sessions(session, adapter, apply=False)
    assert [(f.session_id, f.exposed, f.contained) for f in findings] == [(42, True, False)]
    assert findings[0].relaunch_needed is True
    assert core.replaced == []


@pytest.mark.asyncio
async def test_applying_removes_the_public_listener_and_keeps_the_workload(
    session, comp_bio_user, adapter, monkeypatch
):
    await _session(session, comp_bio_user, 42)
    core = _Core(pods=_pods(comp_bio_user, 42), services=[_lb_service(42)])
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)
    findings = await contain_exposed_sessions(session, adapter, apply=True)
    await session.commit()
    assert [(f.session_id, f.exposed, f.contained) for f in findings] == [(42, True, True)]
    service = core.replaced[0]
    assert service.spec.type == "ClusterIP"
    assert all(p.node_port is None for p in service.spec.ports)
    assert service.spec.external_traffic_policy is None
    assert service.metadata.resource_version == "7"  # replaced only if unchanged since read
    # Nothing was deleted: the Pod, its home and outputs stay.
    assert [c for c in core.calls if c[0] == "delete"] == []
    session.expire_all()
    row = (await session.execute(select(ComputeSession).where(ComputeSession.id == 42))).scalar_one()
    assert row.status == "running"
    assert row.access_url is None and row.proxy_url is None
    assert row.provider_metadata["exposure"]["contained_at"]


@pytest.mark.asyncio
async def test_a_service_bioaf_does_not_own_is_never_changed(session, comp_bio_user, adapter, monkeypatch):
    await _session(session, comp_bio_user, 42)
    core = _Core(pods=_pods(comp_bio_user, 42), services=[_lb_service(42, labels={"bioaf.io/session": "99"})])
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)
    findings = await contain_exposed_sessions(session, adapter, apply=True)
    assert findings[0].contained is False and "not verified" in findings[0].detail
    assert core.replaced == []


@pytest.mark.asyncio
async def test_protected_and_finished_sessions_are_left_alone(session, comp_bio_user, adapter, monkeypatch):
    await _session(session, comp_bio_user, 42)
    await _session(session, comp_bio_user, 43, status="stopped")
    core = _Core(pods=_pods(comp_bio_user, 42), services=[_lb_service(42, kind="ClusterIP")])
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)
    findings = await contain_exposed_sessions(session, adapter, apply=True)
    assert [(f.session_id, f.exposed) for f in findings] == [(42, False)]
    assert core.replaced == []


@pytest.mark.asyncio
async def test_an_unreadable_service_is_reported_not_guessed(session, comp_bio_user, adapter, monkeypatch):
    await _session(session, comp_bio_user, 42)
    core = _Core(
        pods=_pods(comp_bio_user, 42),
        services=[_lb_service(42)],
        errors={("read", "service", "bioaf-notebook-svc-42"): 403},
    )
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)
    findings = await contain_exposed_sessions(session, adapter, apply=True)
    assert findings[0].exposed is None and findings[0].contained is False
    assert core.replaced == []
