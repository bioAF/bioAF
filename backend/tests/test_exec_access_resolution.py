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
    async def load():
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
    async def load():
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

    async def capture(target, *, default_namespace, backend, load_config, clients, deadline=10.0):
        cfg = await load_config()
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
