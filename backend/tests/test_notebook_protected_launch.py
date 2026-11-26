"""Plan 03.5A-C: a new interactive session is reachable only through the protected gateway.

No public listener (ClusterIP, never LoadBalancer), Jupyter's own token from a per-session Secret that
the Pod owns, Jupyter served unprivileged under the gateway path, RStudio under the gateway path, and
readiness that comes from the IDE itself.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.platform.notebook_jupyter_auth import (
    GATEWAY_TOKEN_HEADER,
    JUPYTER_CONFIG_DIR,
    JUPYTER_CONFIG_KEY,
    JUPYTER_GATEWAY_CONFIG,
)
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod


@pytest.fixture
def adapter(mode_adapter_factory):
    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._namespace_ready = True
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    return provider


def _spec(session_type="jupyter", **over):
    spec = {
        "session_type": session_type,
        "session_id": 42,
        "user_id": 7,
        "organization_id": 1,
        "resource_profile": "small",
        "cpu_cores": 2,
        "memory_gb": 4,
        "ide_secret": "s3cret-token",
    }
    if session_type == "rstudio":
        spec["session_credentials"] = {"username": "sci", "password_hash": "$2b$12$abc"}
    spec.update(over)
    return spec


def _container(manifest, name="notebook"):
    return next(c for c in manifest["spec"]["containers"] if c["name"] == name)


# -- Manifest -----------------------------------------------------------------------------------


def test_jupyter_has_no_empty_credentials_and_takes_its_token_from_the_session_secret(adapter):
    manifest = adapter._build_pod_manifest(_spec())
    container = _container(manifest)
    command = " ".join(container["command"])
    assert "token=''" not in command and "password=''" not in command
    assert "s3cret-token" not in str(manifest)
    assert "--ServerApp.base_url=/s/42/" in container["command"]
    env = {e["name"]: e for e in container.get("env", [])}
    assert env["JUPYTER_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "bioaf-notebook-access-42", "key": "token"}
    assert manifest["metadata"]["labels"]["bioaf.io/access"] == "gateway"


def test_jupyter_serves_as_the_unprivileged_notebook_user(adapter):
    security = _container(adapter._build_pod_manifest(_spec()))["securityContext"]
    assert security["runAsUser"] == 1000 and security["runAsGroup"] == 100
    assert security["runAsNonRoot"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert "--allow-root" not in " ".join(_container(adapter._build_pod_manifest(_spec()))["command"])


def test_jupyter_readiness_comes_from_the_jupyter_server_itself(adapter):
    container = _container(adapter._build_pod_manifest(_spec()))
    for probe in ("readinessProbe", "startupProbe"):
        assert container[probe]["httpGet"] == {"path": "/s/42/api", "port": 8888}
    # The startup probe allows image initialization within the existing 30-minute launch deadline.
    startup = container["startupProbe"]
    assert startup["periodSeconds"] * startup["failureThreshold"] <= 30 * 60


def test_rstudio_is_served_under_the_gateway_path_with_a_sign_in_readiness_probe(adapter):
    container = _container(adapter._build_pod_manifest(_spec("rstudio")))
    command = container["command"][-1]
    assert "/usr/lib/rstudio-server/bin/rserver" in command
    assert "--www-root-path=/s/42/" in command
    assert container["readinessProbe"]["httpGet"] == {"path": "/auth-sign-in", "port": 8787}
    # RStudio keeps PAM sign-in with the session credential: no IDE token is added.
    assert "JUPYTER_TOKEN" not in str(container.get("env", []))


def test_a_jupyter_spec_without_a_secret_still_gets_a_nonempty_one(adapter):
    manifest = adapter._build_pod_manifest(_spec(ide_secret=None))
    env = {e["name"]: e for e in _container(manifest).get("env", [])}
    assert "secretKeyRef" in env["JUPYTER_TOKEN"]["valueFrom"]


# -- Launch -------------------------------------------------------------------------------------


class _LaunchCore(FakeCore):
    def __init__(self, fail_pod=False):
        super().__init__()
        self.calls = []
        self.fail_pod = fail_pod
        self.secrets = {}

    def read_namespace(self, *a, **k):
        return SimpleNamespace()

    def create_namespaced_secret(self, namespace, body, **_):
        self.calls.append(("create_secret", body))
        self.secrets[body["metadata"]["name"]] = body
        return SimpleNamespace(metadata=SimpleNamespace(uid="secret-uid"))

    def patch_namespaced_secret(self, name, namespace, body, **_):
        self.calls.append(("patch_secret", body))
        return SimpleNamespace()

    def delete_namespaced_secret(self, name, namespace, **_):
        self.calls.append(("delete_secret", name))
        return SimpleNamespace()

    def create_namespaced_pod(self, namespace, body, **_):
        self.calls.append(("create_pod", body))
        if self.fail_pod:
            from kubernetes.client.exceptions import ApiException

            raise ApiException(status=500)
        return SimpleNamespace(metadata=SimpleNamespace(uid="pod-uid-9", name=body["metadata"]["name"]))

    def create_namespaced_service(self, namespace, body, **_):
        self.calls.append(("create_service", body))
        return SimpleNamespace()


async def _launch(adapter, monkeypatch, core, spec):
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)
    monkeypatch.setattr(adapter, "_ensure_gcs_secret", lambda ns: False)

    async def ready_namespace(*a, **k):
        return None

    monkeypatch.setattr(adapter, "ensure_notebook_namespace", ready_namespace)
    return await adapter._k8s_launch_session(spec)


@pytest.mark.asyncio
async def test_launch_creates_the_secret_first_and_binds_it_to_the_pod(adapter, monkeypatch):
    core = _LaunchCore()
    await _launch(adapter, monkeypatch, core, _spec())
    kinds = [c[0] for c in core.calls]
    assert kinds.index("create_secret") < kinds.index("create_pod") < kinds.index("patch_secret")
    secret = core.secrets["bioaf-notebook-access-42"]
    assert secret["stringData"]["token"] == "s3cret-token"
    assert secret["stringData"][JUPYTER_CONFIG_KEY] == JUPYTER_GATEWAY_CONFIG
    assert secret["metadata"]["labels"]["bioaf.io/session"] == "42"
    patch = next(body for kind, body in core.calls if kind == "patch_secret")
    owner = patch["metadata"]["ownerReferences"][0]
    assert (owner["kind"], owner["name"], owner["uid"]) == ("Pod", "bioaf-notebook-42", "pod-uid-9")


@pytest.mark.asyncio
@pytest.mark.parametrize("session_type", ["jupyter", "rstudio"])
async def test_no_public_listener_is_created(adapter, monkeypatch, session_type):
    core = _LaunchCore()
    await _launch(adapter, monkeypatch, core, _spec(session_type))
    services = [body for kind, body in core.calls if kind == "create_service"]
    assert services and all(s["spec"]["type"] == "ClusterIP" for s in services)


@pytest.mark.asyncio
async def test_a_failed_pod_creation_removes_the_secret(adapter, monkeypatch):
    core = _LaunchCore(fail_pod=True)
    with pytest.raises(Exception):
        await _launch(adapter, monkeypatch, core, _spec())
    assert ("delete_secret", "bioaf-notebook-access-42") in core.calls


# -- Readiness ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_ready_protected_session_is_running_at_its_gateway_path_without_any_address(adapter):
    labels = {
        "bioaf.io/session": "42",
        "bioaf.io/user": "7",
        "bioaf.io/type": "jupyter",
        "bioaf.io/access": "gateway",
    }
    core = FakeCore(pods=[notebook_pod(labels=labels)])
    adapter._get_k8s_core_client = lambda: core
    adapter._resolve_service_url = MagicMock(return_value=None)
    result = await adapter._k8s_get_session_status(
        session_id=42, pod_name="bioaf-notebook-42", namespace="bioaf-notebooks", session_type="jupyter"
    )
    assert (result["status"], result["access_url"]) == ("running", "/s/42/")
    adapter._resolve_service_url.assert_not_called()


@pytest.mark.asyncio
async def test_a_protected_session_whose_ide_is_not_ready_stays_starting(adapter):
    labels = {"bioaf.io/session": "42", "bioaf.io/user": "7", "bioaf.io/type": "jupyter", "bioaf.io/access": "gateway"}
    core = FakeCore(pods=[notebook_pod(labels=labels, ready=False)])
    adapter._get_k8s_core_client = lambda: core
    result = await adapter._k8s_get_session_status(
        session_id=42, pod_name="bioaf-notebook-42", namespace="bioaf-notebooks", session_type="jupyter"
    )
    assert result["status"] == "starting"


# -- Service ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_jupyter_launch_records_its_own_secret_and_hands_it_to_the_adapter(
    session, admin_user, client, admin_token
):
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import text

    from app.adapters.models import ServiceState, SessionInfo
    from app.services.notebook_service import NotebookService

    adapter = MagicMock()
    adapter.reserve_workload = MagicMock(return_value=None)
    adapter.launch_session = AsyncMock(return_value=SessionInfo(session_id="1", status=ServiceState.STARTING))
    with patch("app.services.notebook_service.get_notebook_adapter", return_value=adapter):
        ns = await NotebookService.launch_session(
            session,
            user_id=admin_user.id,
            org_id=admin_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
        )
        await session.commit()
    sid, model = ns.id, type(ns)
    spec = adapter.launch_session.call_args.args[0]
    assert len(spec["ide_secret"]) >= 32
    raw = (
        await session.execute(text("SELECT notebook_access_secret FROM compute_sessions WHERE id = :i"), {"i": sid})
    ).scalar_one()
    assert raw.startswith("gAAAA") and spec["ide_secret"] not in raw
    session.expire_all()
    assert (await session.get(model, sid)).notebook_access_secret == spec["ide_secret"]

    for path in ("/api/v1/notebooks/sessions", f"/api/v1/notebooks/sessions/{sid}"):
        body = (await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})).text
        assert spec["ide_secret"] not in body


@pytest.mark.parametrize("session_type", ["jupyter", "rstudio", "headless"])
def test_notebook_code_gets_no_kubernetes_api_credential(adapter, session_type):
    """Plan 03.5A isolation: the runner account may act in the shared notebook namespace, so its token is
    never mounted where user code runs; cloud identity does not use it (GKE Workload Identity, EKS IRSA)."""
    extra = {"notebook_json": {"cells": []}} if session_type == "headless" else {}
    manifest = adapter._build_pod_manifest(_spec(session_type, **extra))
    assert manifest["spec"]["automountServiceAccountToken"] is False
    assert manifest["spec"]["serviceAccountName"] == "bioaf-notebook-runner"


def test_jupyter_reads_its_token_from_the_gateway_header_through_config_from_its_secret(adapter):
    manifest = adapter._build_pod_manifest(_spec())
    container = _container(manifest)
    env = {e["name"]: e for e in container["env"]}
    assert env["JUPYTER_CONFIG_PATH"]["value"] == JUPYTER_CONFIG_DIR
    mount = next(m for m in container["volumeMounts"] if m["mountPath"] == JUPYTER_CONFIG_DIR)
    assert mount["readOnly"] is True
    volume = next(v for v in manifest["spec"]["volumes"] if v["name"] == mount["name"])
    # Only the config key, not the token, is mounted from the session's Secret.
    assert volume["secret"] == {
        "secretName": "bioaf-notebook-access-42",
        "items": [{"key": JUPYTER_CONFIG_KEY, "path": "jupyter_server_config.py"}],
    }


def test_the_gateway_config_reads_only_the_gateway_header():
    assert GATEWAY_TOKEN_HEADER in JUPYTER_GATEWAY_CONFIG
    assert "identity_provider_class" in JUPYTER_GATEWAY_CONFIG
    compile(JUPYTER_GATEWAY_CONFIG, "jupyter_server_config.py", "exec")


def test_rstudio_gets_no_jupyter_config(adapter):
    container = _container(adapter._build_pod_manifest(_spec("rstudio")))
    assert "JUPYTER_CONFIG_PATH" not in str(container.get("env", []))
    assert all(m["mountPath"] != JUPYTER_CONFIG_DIR for m in container["volumeMounts"])
