"""The two neutral helpers several adapters repeated, now shared.

- The pod-identity annotation check-and-patch on a runner service account (compute, notebook and
  cellxgene Kubernetes adapters), tested against an in-memory service-account API so the
  observable annotation state, not a call count, is what is checked.
- The readiness poller's compute-session update (notebook Kubernetes, GCE and EC2 work nodes),
  tested against real rows. It is now scoped by session id AND organization: the pollers carry the
  organization of the server-created session they were started for, and an update naming another
  organization's session changes nothing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from kubernetes.client.rest import ApiException
from sqlalchemy import select

from app.adapters.kubernetes.service_account import ensure_service_account_annotations
from app.models.notebook_session import ComputeSession
from app.models.organization import Organization


class FakeServiceAccounts:
    """Enough of CoreV1Api for read/patch of one namespace's service accounts."""

    def __init__(self, accounts: dict[str, dict] | None = None, fail_patch: bool = False):
        self.accounts = accounts if accounts is not None else {}
        self.fail_patch = fail_patch
        self.patches = 0

    def read_namespaced_service_account(self, name, namespace):
        if name not in self.accounts:
            raise ApiException(status=404, reason="Not Found")
        sa = MagicMock()
        sa.metadata.annotations = dict(self.accounts[name]) or None
        return sa

    def patch_namespaced_service_account(self, name, namespace, body):
        self.patches += 1
        if self.fail_patch:
            raise ApiException(status=500, reason="boom")
        self.accounts[name].update(body["metadata"]["annotations"])


DESIRED = {"iam.gke.io/gcp-service-account": "runner@p.iam.gserviceaccount.com"}


class TestAnnotationHelper:
    def test_a_missing_annotation_is_added(self):
        api = FakeServiceAccounts({"ksa": {"other": "x"}})
        ensure_service_account_annotations(api, namespace="ns", service_account="ksa", desired=DESIRED)
        assert api.accounts["ksa"] == {"other": "x", **DESIRED}

    def test_an_already_correct_account_is_not_patched(self):
        api = FakeServiceAccounts({"ksa": dict(DESIRED)})
        ensure_service_account_annotations(api, namespace="ns", service_account="ksa", desired=DESIRED)
        assert api.patches == 0

    def test_no_identity_means_nothing_to_do(self):
        api = FakeServiceAccounts({"ksa": {}})
        ensure_service_account_annotations(api, namespace="ns", service_account="ksa", desired={})
        assert api.patches == 0 and api.accounts["ksa"] == {}

    def test_a_missing_account_or_a_failed_patch_is_logged_not_raised(self, caplog):
        ensure_service_account_annotations(
            FakeServiceAccounts({}), namespace="ns", service_account="ksa", desired=DESIRED
        )
        failing = FakeServiceAccounts({"ksa": {}}, fail_patch=True)
        ensure_service_account_annotations(failing, namespace="ns", service_account="ksa", desired=DESIRED)
        assert failing.accounts["ksa"] == {}
        assert caplog.text.count("Failed to patch pod-identity annotation on ksa") == 2


@pytest.mark.parametrize(
    "module,cls,ksa",
    [
        ("app.adapters.compute.kubernetes", "KubernetesComputeProvider", "bioaf-pipeline-runner"),
        ("app.adapters.notebooks.kubernetes", "KubernetesNotebookProvider", "bioaf-notebook-runner"),
        ("app.adapters.cellxgene.kubernetes", "KubernetesCellxgeneProvider", "bioaf-cellxgene-runner"),
    ],
)
def test_each_kubernetes_adapter_annotates_its_own_runner(module, cls, ksa):
    import importlib

    provider = getattr(importlib.import_module(module), cls)()
    api = FakeServiceAccounts({ksa: {}})
    provider._patch_sa_annotation(api, "ns", "runner@p.iam.gserviceaccount.com")
    assert api.accounts[ksa] == provider._pod_identity.pod_identity_annotations("runner@p.iam.gserviceaccount.com")


# --- compute-session updates ------------------------------------------------------------------


@pytest_asyncio.fixture
async def sessions(session, admin_user):
    other = Organization(name="Other", setup_complete=True)
    session.add(other)
    await session.flush()

    def _row(org_id):
        return ComputeSession(
            user_id=admin_user.id,
            organization_id=org_id,
            session_type="jupyter",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=4,
            status="starting",
        )

    ours, theirs = _row(admin_user.organization_id), _row(other.id)
    session.add_all([ours, theirs])
    await session.commit()
    return ours, theirs


def _adapters():
    import app.database as database
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
    from app.adapters.work_nodes.ec2 import Ec2WorkNodeProvider
    from app.adapters.work_nodes.gce import GCEWorkNodeProvider

    factory = database.async_session_factory
    return [
        KubernetesNotebookProvider(session_factory=factory),
        GCEWorkNodeProvider(session_factory=factory),
        Ec2WorkNodeProvider(session_factory=factory),
    ]


async def _reload(session, row):
    """The row as persisted (columns read fresh, not the identity-mapped instance)."""
    from types import SimpleNamespace

    cols = (
        ComputeSession.status,
        ComputeSession.access_url,
        ComputeSession.failure_reason,
        ComputeSession.failure_message,
    )
    values = (await session.execute(select(*cols).where(ComputeSession.id == row.id))).one()
    return SimpleNamespace(**dict(zip(("status", "access_url", "failure_reason", "failure_message"), values)))


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1, 2], ids=["notebook", "gce", "ec2"])
async def test_the_update_writes_status_and_url_and_only_given_failure_fields(session, sessions, index):
    ours, _ = sessions
    adapter = _adapters()[index]
    await adapter._update_session_in_db(ours.organization_id, ours.id, status="running", access_url="http://x")
    row = await _reload(session, ours)
    assert (row.status, row.access_url, row.failure_reason) == ("running", "http://x", None)

    await adapter._update_session_in_db(
        ours.organization_id, ours.id, status="failed", access_url=None, failure_reason="quota", failure_message="m"
    )
    row = await _reload(session, ours)
    assert (row.status, row.access_url, row.failure_reason, row.failure_message) == ("failed", None, "quota", "m")

    await adapter._update_session_in_db(ours.organization_id, ours.id, status="stopped", access_url=None)
    row = await _reload(session, ours)
    assert (row.status, row.failure_reason) == ("stopped", "quota")  # not clobbered


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1, 2], ids=["notebook", "gce", "ec2"])
async def test_an_update_naming_another_organizations_session_changes_nothing(session, sessions, index):
    ours, theirs = sessions
    adapter = _adapters()[index]
    await adapter._update_session_in_db(ours.organization_id, theirs.id, status="running", access_url="http://evil")
    row = await _reload(session, theirs)
    assert (row.status, row.access_url) == ("starting", None)


@pytest.mark.asyncio
async def test_without_a_session_factory_nothing_is_written(session, sessions):
    from app.adapters.work_nodes.gce import GCEWorkNodeProvider

    ours, _ = sessions
    await GCEWorkNodeProvider(session_factory=None)._update_session_in_db(
        ours.organization_id, ours.id, status="running", access_url="x"
    )
    assert (await _reload(session, ours)).status == "starting"


@pytest.mark.parametrize(
    "path",
    [
        "app/services/notebook_service.py",
        "app/services/notebook_execution_service.py",
        "app/services/work_node_service.py",
    ],
)
def test_every_launch_spec_carries_the_sessions_organization(path):
    """The pollers can only scope by organization if the server-created session's organization
    reaches them; each launch spec is built from that row."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / path).read_text()
    assert '"organization_id":' in source
