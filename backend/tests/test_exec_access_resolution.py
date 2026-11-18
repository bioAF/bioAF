"""Live target checks behind terminal instructions.

Uses real ``kubernetes.client`` model objects shaped like what launch creates (the Job from
``k8s_manifest``, the Pod from the notebook adapter) and a fake API that answers only metadata
reads: any other call, including pod exec, fails the test.
"""

import asyncio

import pytest
from kubernetes.client import rest as k8s_rest
from kubernetes.client.exceptions import ApiException

from tests.support.exec_access_fakes import (
    CONTEXT,
    GKE,
    JOB,
    FakeK8s,
    head_pod,
    notebook_pod,
    pipeline_job,
    task_pod,
)
from app.adapters.capabilities import CapabilityNotSupported
from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.kubernetes import exec_access
from app.adapters.kubernetes.exec_access import resolve_connection
from app.adapters.models import ConnectionTarget
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.exceptions import (
    ConnectionProviderTimeoutError,
    ConnectionProviderUnavailableError,
    ConnectionTargetInvalidError,
    ConnectionUnavailableError,
)
from app.settings_contract import ComputeMode

PIPE = ConnectionTarget(kind="pipeline_job", record_id=42, provider_ref=JOB, namespace=None)
NB = ConnectionTarget(
    kind="notebook_session",
    record_id=7,
    provider_ref="bioaf-notebook-7",
    namespace=None,
    user_id=3,
    session_type="jupyter",
)


async def _resolve(target, fake, *, default_namespace=None, cfg=GKE, backend="gke", deadline=10.0):
    async def load(budget):
        return cfg

    ns = default_namespace or ("bioaf-pipelines" if target.kind == "pipeline_job" else "bioaf-notebooks")
    return await resolve_connection(
        target, default_namespace=ns, backend=backend, load_config=load, clients=lambda: (fake, fake), deadline=deadline
    )


# -- pipelines ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_resolves_the_jobs_own_head_pod_and_pipeline_container():
    fake = FakeK8s(job=pipeline_job(), pods=[task_pod(), head_pod()])
    d = await _resolve(PIPE, fake)

    assert d.provider_ref == JOB
    assert d.pod_name == f"{JOB}-x7k2p"
    assert (d.namespace, d.container, d.context) == ("bioaf-pipelines", "pipeline", CONTEXT)
    assert d.command == (
        f"kubectl exec -it --context {CONTEXT} -n bioaf-pipelines {JOB}-x7k2p --container pipeline -- /bin/sh"
    )
    assert "get-credentials" in d.setup_guide


@pytest.mark.asyncio
async def test_pipeline_uses_the_recorded_nondefault_namespace():
    fake = FakeK8s(job=pipeline_job(), pods=[head_pod()])
    d = await _resolve(PIPE.model_copy(update={"namespace": "team-pipelines"}), fake)
    assert d.namespace == "team-pipelines"
    assert {c[1]["namespace"] for c in fake.calls} == {"team-pipelines"}


@pytest.mark.asyncio
async def test_a_job_labelled_for_another_run_is_refused():
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        await _resolve(PIPE, FakeK8s(job=pipeline_job(run_id="43"), pods=[head_pod()]))
    assert exc.value.reason == "identity_mismatch"


@pytest.mark.asyncio
async def test_a_pod_owned_by_a_different_job_uid_is_not_thehead_pod():
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(PIPE, FakeK8s(job=pipeline_job(), pods=[head_pod(owner_uid="other-uid"), task_pod()]))
    assert exc.value.reason == "container_not_running"


@pytest.mark.asyncio
async def test_a_stopped_pipeline_container_with_a_running_sidecar_is_unavailable():
    pod = head_pod(pipeline="terminated", sidecars=(("log-shipper", "running"),))
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(PIPE, FakeK8s(job=pipeline_job(), pods=[pod]))
    assert exc.value.reason == "container_not_running"


@pytest.mark.asyncio
async def test_two_eligible_head_pods_are_ambiguous():
    pods = [head_pod(name=f"{JOB}-a"), head_pod(name=f"{JOB}-b")]
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(PIPE, FakeK8s(job=pipeline_job(), pods=pods))
    assert exc.value.reason == "ambiguous_target"


@pytest.mark.asyncio
async def test_a_deleting_job_or_pod_is_ineligible():
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(PIPE, FakeK8s(job=pipeline_job(deleting=True), pods=[head_pod()]))
    assert exc.value.reason == "workload_ending"
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(PIPE, FakeK8s(job=pipeline_job(), pods=[head_pod(deleting=True)]))
    assert exc.value.reason == "container_not_running"


@pytest.mark.asyncio
async def test_a_missing_job_is_unavailable():
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(PIPE, FakeK8s(job=None))
    assert exc.value.reason == "workload_missing"


# -- notebooks ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notebook_resolves_its_pod_and_notebook_container():
    fake = FakeK8s(pod=notebook_pod())
    d = await _resolve(NB, fake)
    assert (d.provider_ref, d.pod_name, d.namespace, d.container) == (
        "bioaf-notebook-7",
        "bioaf-notebook-7",
        "bioaf-notebooks",
        "notebook",
    )
    assert "--container notebook -- /bin/sh" in d.command


@pytest.mark.parametrize(
    "labels",
    [{"session": "8", "name": "bioaf-notebook-7"}, {"user": "4"}, {"kind": "rstudio"}],
)
@pytest.mark.asyncio
async def test_notebook_pod_labels_must_match_the_record(labels):
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        await _resolve(NB, FakeK8s(pod=notebook_pod(**labels)))
    assert exc.value.reason == "identity_mismatch"


@pytest.mark.asyncio
async def test_notebook_pod_without_identity_labels_is_refused():
    pod = notebook_pod()
    pod.metadata.labels = {"bioaf.io/pool": "interactive"}
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        await _resolve(NB, FakeK8s(pod=pod))
    assert exc.value.reason == "identity_mismatch"


@pytest.mark.parametrize("state", ["waiting", "terminated"])
@pytest.mark.asyncio
async def test_a_running_sync_sidecar_does_not_make_a_stopped_notebook_reachable(state):
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(NB, FakeK8s(pod=notebook_pod(notebook=state)))
    assert exc.value.reason == "container_not_running"


@pytest.mark.asyncio
async def test_a_deleting_notebook_pod_is_ineligible():
    with pytest.raises(ConnectionUnavailableError) as exc:
        await _resolve(NB, FakeK8s(pod=notebook_pod(deleting=True)))
    assert exc.value.reason == "workload_ending"


# -- provider failures ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error,exc_type,reason",
    [
        (ApiException(status=403, reason="Forbidden"), ConnectionProviderUnavailableError, "provider_denied"),
        (ApiException(status=401, reason="Unauthorized"), ConnectionProviderUnavailableError, "provider_denied"),
        (ApiException(status=404, reason="Not Found"), ConnectionUnavailableError, "workload_missing"),
        (ApiException(status=503, reason="Unavailable"), ConnectionProviderUnavailableError, "provider_unavailable"),
        (
            k8s_rest.urllib3.exceptions.NewConnectionError(None, "refused"),
            ConnectionProviderUnavailableError,
            "provider_unavailable",
        ),
        (
            k8s_rest.urllib3.exceptions.ReadTimeoutError(None, "/", "timed out"),
            ConnectionProviderTimeoutError,
            "deadline_exceeded",
        ),
        (
            k8s_rest.urllib3.exceptions.MaxRetryError(
                None, "/", k8s_rest.urllib3.exceptions.ConnectTimeoutError(None, "connect timed out")
            ),
            ConnectionProviderTimeoutError,
            "deadline_exceeded",
        ),
        (
            k8s_rest.urllib3.exceptions.MaxRetryError(
                None, "/", k8s_rest.urllib3.exceptions.NewConnectionError(None, "refused")
            ),
            ConnectionProviderUnavailableError,
            "provider_unavailable",
        ),
    ],
)
@pytest.mark.parametrize("target", [PIPE, NB])
@pytest.mark.asyncio
async def test_provider_failures_map_to_safe_distinct_errors(target, error, exc_type, reason, caplog):
    with pytest.raises(exc_type) as exc:
        await _resolve(target, FakeK8s(error=error))
    assert exc.value.reason == reason
    assert "Forbidden" not in str(exc.value) and "Unavailable" not in str(exc.value)


@pytest.mark.asyncio
async def test_resolution_is_bounded_by_its_deadline():
    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(ConnectionProviderTimeoutError) as exc:
        await _resolve(NB, FakeK8s(pod=notebook_pod(), delay=2.0), deadline=0.3)
    assert exc.value.status_code == 504
    assert loop.time() - start < 1.5


@pytest.mark.asyncio
async def test_a_failure_building_the_cluster_client_is_a_provider_outage():
    async def load(budget):
        return GKE

    def clients():
        raise RuntimeError("No cluster endpoint in platform_config")

    with pytest.raises(ConnectionProviderUnavailableError) as exc:
        await resolve_connection(
            NB, default_namespace="bioaf-notebooks", backend="gke", load_config=load, clients=clients
        )
    assert exc.value.reason == "provider_unavailable"


@pytest.mark.asyncio
async def test_missing_cluster_setup_is_refused_before_any_provider_read():
    fake = FakeK8s(pod=notebook_pod())
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        await _resolve(NB, fake, cfg={"gke_cluster_name": "c"})
    assert exc.value.reason == "cluster_setup_incomplete"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_eks_cluster_produces_aws_setup_and_alias_context():
    cfg = {"gke_cluster_name": "bioaf-lab-xyz789", "aws_region": "eu-west-2", "aws_account_id": "123456789012"}
    d = await _resolve(NB, FakeK8s(pod=notebook_pod()), cfg=cfg, backend="eks")
    assert d.context == "bioaf-123456789012-eu-west-2-bioaf-lab-xyz789"
    assert f"--context {d.context}" in d.command
    assert "gcloud" not in d.setup_guide and "update-kubeconfig" in d.setup_guide


# -- adapters ------------------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls", [KubernetesComputeProvider, KubernetesNotebookProvider])
@pytest.mark.asyncio
async def test_simulated_mode_never_returns_a_kubernetes_command(adapter_cls):
    adapter = adapter_cls(mode=ComputeMode.LOCAL)
    target = PIPE if adapter_cls is KubernetesComputeProvider else NB
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        await adapter.describe_connection(target)
    assert exc.value.reason == "simulated_mode"


@pytest.mark.asyncio
async def test_adapters_without_a_live_resolver_report_the_capability_unsupported():
    from app.adapters.compute.slurm import SlurmComputeProvider
    from app.adapters.notebooks.slurm import SlurmNotebookProvider

    with pytest.raises(CapabilityNotSupported):
        await SlurmComputeProvider().describe_connection(PIPE)
    with pytest.raises(CapabilityNotSupported):
        await SlurmNotebookProvider().describe_connection(NB)


def _live(adapter, fake, monkeypatch):
    adapter._cluster_config = GKE
    monkeypatch.setattr(adapter, "_connection_clients", lambda: (fake, fake))
    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "gke")
    return adapter


@pytest.mark.asyncio
async def test_notebook_adapter_falls_back_to_its_launch_rule_for_an_unrecorded_pod(monkeypatch):
    adapter = _live(KubernetesNotebookProvider(mode=ComputeMode.KUBERNETES), FakeK8s(pod=notebook_pod()), monkeypatch)
    d = await adapter.describe_connection(NB.model_copy(update={"provider_ref": None}))
    assert d.pod_name == "bioaf-notebook-7"  # one prefix, never bioaf-notebook-notebook-7
    assert d.namespace == "bioaf-notebooks"


@pytest.mark.asyncio
async def test_compute_adapter_defaults_to_its_launch_namespace(monkeypatch):
    fake = FakeK8s(job=pipeline_job(), pods=[head_pod()])
    adapter = _live(KubernetesComputeProvider(mode=ComputeMode.KUBERNETES), fake, monkeypatch)
    d = await adapter.describe_connection(PIPE)
    assert d.namespace == "bioaf-pipelines"
    assert d.pod_name == f"{JOB}-x7k2p"


@pytest.mark.asyncio
async def test_compute_adapter_refuses_a_pipeline_target_without_apipeline_job():
    adapter = KubernetesComputeProvider(mode=ComputeMode.KUBERNETES)
    adapter._cluster_config = GKE
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        await adapter.describe_connection(PIPE.model_copy(update={"provider_ref": None}))
    assert exc.value.reason == "missing_identity"


@pytest.mark.parametrize("adapter_cls", [KubernetesComputeProvider, KubernetesNotebookProvider])
@pytest.mark.asyncio
async def test_the_check_reads_the_same_cluster_the_command_names_after_a_config_change(adapter_cls, monkeypatch):
    """A cluster redeploy rewrites platform_config; the cached client must not keep reading the old one.

    The live check (client) and the command/setup (config) come from one refreshed cluster identity.
    """
    from types import SimpleNamespace

    from app.adapters.kubernetes import connection as k8s_connection

    stored = {}
    cluster_a = {**GKE, "gke_cluster_endpoint": "10.0.0.1", "gke_cluster_ca_cert": "Y2EtYQ=="}
    cluster_b = {
        **GKE,
        "gke_cluster_name": "bioaf-lab-new999",
        "gke_cluster_endpoint": "10.0.0.2",
        "gke_cluster_ca_cert": "Y2EtYg==",
    }

    adapter = adapter_cls(session_factory=object(), mode=ComputeMode.KUBERNETES)
    gke = adapter._gke

    async def load_cluster_config(force=False):
        gke._cluster_config = dict(stored)
        if force and gke._invalidate_client_on_force:
            gke._api_client = None
        return gke._cluster_config

    def not_in_cluster():
        raise k8s_connection.config.ConfigException("not in cluster")

    monkeypatch.setattr(gke, "load_cluster_config", load_cluster_config)
    monkeypatch.setattr(
        gke,
        "build_out_of_cluster_client",
        lambda: SimpleNamespace(endpoint=gke._cluster_config["gke_cluster_endpoint"]),
    )
    monkeypatch.setattr(k8s_connection.config, "load_incluster_config", not_in_cluster)

    seen = []

    async def capture(target, *, default_namespace, backend, load_config, clients, deadline=None):
        cfg = await load_config(10.0)
        core, _batch = clients()
        seen.append((cfg["gke_cluster_endpoint"], core.api_client.endpoint))

    monkeypatch.setattr(exec_access, "resolve_connection", capture)
    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "gke")
    target = PIPE if adapter_cls is KubernetesComputeProvider else NB

    stored.update(cluster_a)
    await adapter.describe_connection(target)
    stored.clear()
    stored.update(cluster_b)
    await adapter.describe_connection(target)

    assert seen == [("10.0.0.1", "10.0.0.1"), ("10.0.0.2", "10.0.0.2")]


def _notebook_with_cluster_client(monkeypatch, build):
    """A live notebook adapter whose cluster refresh calls ``build`` to make the client."""
    from app.adapters.kubernetes import connection as k8s_connection

    adapter = KubernetesNotebookProvider(session_factory=object(), mode=ComputeMode.KUBERNETES)
    gke = adapter._gke

    async def load_cluster_config(force=False):
        gke._cluster_config = {**GKE, "gke_cluster_endpoint": "10.0.0.9", "gke_cluster_ca_cert": "Y2E="}
        return gke._cluster_config

    def not_in_cluster():
        raise k8s_connection.config.ConfigException("not in cluster")

    monkeypatch.setattr(gke, "load_cluster_config", load_cluster_config)
    monkeypatch.setattr(gke, "build_out_of_cluster_client", build)
    monkeypatch.setattr(k8s_connection.config, "load_incluster_config", not_in_cluster)
    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "gke")
    return adapter


@pytest.mark.asyncio
async def test_a_notebook_credential_failure_is_a_structured_provider_error(monkeypatch):
    def build():
        raise RuntimeError("token mint failed: secret detail")

    adapter = _notebook_with_cluster_client(monkeypatch, build)
    with pytest.raises(ConnectionProviderUnavailableError) as exc:
        await adapter.describe_connection(NB)
    assert "secret detail" not in str(exc.value)


@pytest.mark.asyncio
async def test_a_slow_notebook_credential_refresh_stays_within_the_deadline(monkeypatch):
    import time

    def build():
        time.sleep(1.0)
        raise AssertionError("unreachable within the deadline")

    adapter = _notebook_with_cluster_client(monkeypatch, build)
    monkeypatch.setattr(exec_access, "RESOLUTION_DEADLINE_SECONDS", 0.2)
    started = time.monotonic()
    with pytest.raises(ConnectionProviderTimeoutError):
        await adapter.describe_connection(NB)
    assert time.monotonic() - started < 0.8


def _listing(*clusters, missing_zones=()):
    from types import SimpleNamespace

    return SimpleNamespace(
        clusters=[SimpleNamespace(name=n, location=loc) for n, loc in clusters], missing_zones=list(missing_zones)
    )


@pytest.mark.parametrize("adapter_cls", [KubernetesComputeProvider, KubernetesNotebookProvider])
@pytest.mark.asyncio
async def test_an_install_without_a_recorded_location_discovers_it_for_terminal_setup(adapter_cls, monkeypatch):
    is_compute = adapter_cls is KubernetesComputeProvider
    fake = FakeK8s(job=pipeline_job(), pods=[head_pod()]) if is_compute else FakeK8s(pod=notebook_pod())
    adapter = _live(adapter_cls(mode=ComputeMode.KUBERNETES), fake, monkeypatch)
    adapter._cluster_config = {k: v for k, v in GKE.items() if k != "gke_cluster_location"}
    calls = []

    def list_clusters(parent, timeout):
        calls.append((parent, timeout))
        return _listing((GKE["gke_cluster_name"], "europe-west1-b"))

    monkeypatch.setattr(adapter, "_gke_list_clusters", list_clusters)

    d = await adapter.describe_connection(PIPE if is_compute else NB)

    [(parent, timeout)] = calls
    assert parent == "projects/lab-proj-1/locations/-"
    # The listing gets what is left of the resolver's 10 s budget, not the SDK's own 20 s default.
    assert 0 < timeout <= exec_access.RESOLUTION_DEADLINE_SECONDS
    assert d.context == "gke_lab-proj-1_europe-west1-b_bioaf-lab-abc123"
    assert "--zone europe-west1-b" in d.setup_guide


def _raise(exc):
    def list_clusters(parent, timeout):
        raise exc

    return list_clusters


@pytest.mark.parametrize("adapter_cls", [KubernetesComputeProvider, KubernetesNotebookProvider])
@pytest.mark.parametrize(
    "listing,expected,reason",
    [
        (lambda parent, timeout: _listing(), ConnectionTargetInvalidError, "cluster_setup_incomplete"),
        (
            lambda parent, timeout: _listing((GKE["gke_cluster_name"], "us-west1"), missing_zones=["asia-east1-a"]),
            ConnectionTargetInvalidError,
            "cluster_setup_incomplete",
        ),
        (
            _raise(__import__("google.api_core.exceptions").api_core.exceptions.PermissionDenied("secret")),
            ConnectionProviderUnavailableError,
            "provider_denied",
        ),
        (
            _raise(__import__("google.api_core.exceptions").api_core.exceptions.DeadlineExceeded("secret")),
            ConnectionProviderTimeoutError,
            "deadline_exceeded",
        ),
        (_raise(RuntimeError("secret")), ConnectionProviderUnavailableError, "provider_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_terminal_setup_refuses_when_the_location_cannot_be_discovered(
    adapter_cls, listing, expected, reason, monkeypatch
):
    adapter = _live(adapter_cls(mode=ComputeMode.KUBERNETES), FakeK8s(), monkeypatch)
    adapter._cluster_config = {k: v for k, v in GKE.items() if k != "gke_cluster_location"}
    monkeypatch.setattr(adapter, "_gke_list_clusters", listing)

    target = PIPE if adapter_cls is KubernetesComputeProvider else NB
    with pytest.raises(expected) as exc:
        await adapter.describe_connection(target)
    assert exc.value.reason == reason
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("adapter_cls", [KubernetesComputeProvider, KubernetesNotebookProvider])
@pytest.mark.asyncio
async def test_the_listing_call_carries_the_budget_and_no_sdk_retries(adapter_cls, monkeypatch):
    from unittest.mock import MagicMock

    adapter = adapter_cls(mode=ComputeMode.KUBERNETES)
    client = MagicMock()
    client.list_clusters.return_value = _listing()
    if adapter_cls is KubernetesComputeProvider:
        monkeypatch.setattr(adapter, "_get_gke_client", lambda: client)
    else:
        monkeypatch.setattr(adapter, "_gke_cluster_manager", lambda: client)

    response = adapter._gke_list_clusters("projects/p/locations/-", 2.5)

    client.list_clusters.assert_called_once_with(parent="projects/p/locations/-", timeout=2.5, retry=None)
    assert response is client.list_clusters.return_value


@pytest.mark.asyncio
async def test_the_listing_timeout_is_what_remains_when_the_cloud_call_starts():
    """Codex repro: the budget was read before the config refresh, so the SDK got more time than was left."""
    import time

    started = time.monotonic()
    seen = []

    def list_clusters(parent, timeout):
        seen.append((time.monotonic() - started, timeout))
        return _listing((GKE["gke_cluster_name"], "europe-west1-b"))

    async def load(budget):
        await asyncio.sleep(0.4)  # the adapter's config refresh (database reads)
        cfg = {k: v for k, v in GKE.items() if k != "gke_cluster_location"}
        await exec_access.discover_gke_location(cfg, list_clusters=list_clusters, session_factory=None, budget=budget)
        return cfg

    fake = FakeK8s(pod=notebook_pod())
    await resolve_connection(
        NB,
        default_namespace="bioaf-notebooks",
        backend="gke",
        load_config=load,
        clients=lambda: (fake, fake),
        deadline=1.0,
    )

    [(elapsed, timeout)] = seen
    assert elapsed >= 0.4
    assert timeout <= 1.0 - elapsed + 0.01


@pytest.mark.asyncio
async def test_a_budget_spent_before_the_cloud_call_is_a_timeout_not_a_call():
    calls = []
    with pytest.raises(ConnectionProviderTimeoutError) as exc:
        await exec_access.discover_gke_location(
            {"gke_cluster_name": "c", "gcp_project_id": "p"},
            list_clusters=lambda p, t: calls.append(t),
            session_factory=None,
            budget=lambda: exec_access._Deadline(0).remaining(),
        )
    assert exc.value.reason == "deadline_exceeded"
    assert calls == []
