"""Tests for K8s notebook adapter production mode (mocked K8s API).

Tests 1-12 from Phase 22 spec: pod creation, commands, service,
DB updates, GCS sync init, terminate, status, namespace setup.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider


@pytest.fixture
def adapter():
    import time

    provider = KubernetesNotebookProvider()
    provider._mode = "k8s"
    provider._namespace_ready = False
    # Pre-set a mock API client so _get_api_client_async() is a no-op
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    return provider


@pytest.fixture
def mock_k8s_clients():
    """Set up mocked K8s API clients."""
    mock_core = MagicMock()
    mock_rbac = MagicMock()

    # Namespace exists by default
    mock_core.read_namespace.return_value = MagicMock()
    # Pod becomes ready
    mock_pod = MagicMock()
    mock_pod.status.phase = "Running"
    mock_pod.status.conditions = [MagicMock(type="Ready", status="True")]
    mock_core.read_namespaced_pod.return_value = mock_pod
    # Service creation succeeds
    mock_core.create_namespaced_service.return_value = MagicMock()
    mock_core.create_namespaced_pod.return_value = MagicMock()

    return mock_core, mock_rbac


def _session_spec(session_type="jupyter", session_id=42, user_id=7):
    return {
        "session_type": session_type,
        "session_id": session_id,
        "user_id": user_id,
        "resource_profile": "small",
        "cpu_cores": 2,
        "memory_gb": 4,
        "experiment_id": None,
    }


class TestLaunchSession:
    @pytest.mark.asyncio
    async def test_launch_creates_pod(self, adapter, mock_k8s_clients):
        """Test 1: launch_session submits a Pod manifest to K8s."""
        mock_core, mock_rbac = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)
        adapter._poll_session_ready = AsyncMock()

        await adapter._k8s_launch_session(_session_spec())

        mock_core.create_namespaced_pod.assert_called_once()
        pod_body = mock_core.create_namespaced_pod.call_args[1]["body"]
        assert pod_body["metadata"]["labels"]["bioaf.io/pool"] == "interactive"
        assert pod_body["metadata"]["labels"]["bioaf.io/session"] == "42"
        assert pod_body["spec"]["nodeSelector"]["bioaf.io/pool"] == "interactive"

    @pytest.mark.asyncio
    async def test_launch_jupyter_command(self, adapter, mock_k8s_clients):
        """Test 2: Jupyter session uses jupyter lab command."""
        mock_core, mock_rbac = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)
        adapter._poll_session_ready = AsyncMock()

        await adapter._k8s_launch_session(_session_spec("jupyter"))

        pod_body = mock_core.create_namespaced_pod.call_args[1]["body"]
        containers = pod_body["spec"]["containers"]
        cmd_str = " ".join(containers[0].get("command", []))
        assert "jupyter" in cmd_str

    @pytest.mark.asyncio
    async def test_launch_rstudio_command(self, adapter, mock_k8s_clients):
        """Test 3: RStudio session uses rserver command."""
        mock_core, mock_rbac = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)
        adapter._poll_session_ready = AsyncMock()

        spec = _session_spec("rstudio")
        spec["session_credentials"] = {"username": "testuser", "password": "testpass"}
        await adapter._k8s_launch_session(spec)

        pod_body = mock_core.create_namespaced_pod.call_args[1]["body"]
        containers = pod_body["spec"]["containers"]
        cmd_str = " ".join(containers[0].get("command", []))
        assert "rserver" in cmd_str

    @pytest.mark.asyncio
    async def test_launch_creates_service(self, adapter, mock_k8s_clients):
        """Test 4: launch_session creates a K8s Service for the pod."""
        mock_core, mock_rbac = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)
        adapter._poll_session_ready = AsyncMock()

        await adapter._k8s_launch_session(_session_spec())

        mock_core.create_namespaced_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_launch_returns_session_data(self, adapter, mock_k8s_clients):
        """Test 5: launch_session returns pod name and starting status.

        The adapter now returns immediately with status 'starting' and polls
        for pod readiness + LB IP in a background task.
        """
        mock_core, mock_rbac = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)

        # Stub out background poll so it doesn't crash on missing config
        adapter._poll_session_ready = AsyncMock()

        result = await adapter._k8s_launch_session(_session_spec())

        assert "pod_name" in result
        assert "access_url" in result
        assert result["status"] == "starting"
        assert result["pod_name"] == "bioaf-notebook-42"

    @pytest.mark.asyncio
    async def test_launch_includes_gcs_sync_init(self, adapter, mock_k8s_clients):
        """Test 6: Pod manifest includes GCS sync init container."""
        mock_core, mock_rbac = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)
        adapter._poll_session_ready = AsyncMock()

        await adapter._k8s_launch_session(_session_spec())

        pod_body = mock_core.create_namespaced_pod.call_args[1]["body"]
        init_containers = pod_body["spec"].get("initContainers", [])
        assert len(init_containers) >= 1
        init_cmd = " ".join(init_containers[0].get("command", []))
        assert "gsutil" in init_cmd
        assert "rsync" in init_cmd

    def test_pod_manifest_stages_via_s3_on_aws(self, adapter):
        """The data-staging containers must use the cloud-selected storage CLI: an
        S3 install stages inputs/home with `aws s3` + s3:// instead of gsutil +
        gs://. (The notebook pod previously hardcoded a gs:// home prefix and
        `gsutil cp` for inputs, so on AWS the gcs-data-sync init container ran
        gsutil under amazon/aws-cli, exited non-zero, and the pod entered Failed.)"""
        s3 = MagicMock()
        s3.build_uri.side_effect = lambda b, k: f"s3://{b}/{k}"
        s3.staging_image.return_value = "amazon/aws-cli"
        s3.sync_in_command.side_effect = lambda prefix, d: ["/bin/sh", "-c", f"aws s3 sync {prefix} {d} || true"]
        s3.cli_copy_in.side_effect = lambda uri, dest: f"aws s3 cp {uri} {dest}"

        spec = _session_spec("jupyter")
        spec["working_bucket"] = "bioaf-working-5f6286"
        spec["input_files"] = [{"relative_path": "a.fastq.gz", "gcs_uri": "s3://bioaf-raw-5f6286/a.fastq.gz"}]

        with patch("app.adapters.registry.get_storage_adapter", return_value=s3):
            manifest = adapter._build_pod_manifest(spec, has_gcs_secret=False)

        init_containers = manifest["spec"]["initContainers"]
        all_cmds = " ".join(" ".join(ic.get("command", [])) for ic in init_containers)
        assert "aws s3" in all_cmds
        assert "gsutil" not in all_cmds
        assert "gs://" not in all_cmds
        # The input-staging container is present and runs the S3 CLI image.
        staging = {ic["name"]: ic for ic in init_containers if ic["name"] in ("gcs-sync-in", "gcs-data-sync")}
        assert "gcs-data-sync" in staging
        assert all(ic["image"] == "amazon/aws-cli" for ic in staging.values())
        # The staged home prefix is an s3:// URI.
        assert manifest["_gcs_home_prefix"].startswith("s3://")


class TestTerminateSession:
    @pytest.mark.asyncio
    async def test_terminate_syncs_to_gcs(self, adapter, mock_k8s_clients):
        """Test 7: terminate syncs to GCS before pod deletion."""
        mock_core, _ = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)

        with patch("kubernetes.stream.stream") as mock_stream:
            await adapter._k8s_terminate_session(
                session_id=42,
                pod_name="bioaf-notebook-42",
                namespace="bioaf-notebooks",
                gcs_home_prefix="gs://bucket/notebooks/7/",
            )

        # stream is called twice: git commit + GCS sync
        assert mock_stream.call_count == 2
        # Last call should be the GCS sync
        assert "gsutil" in str(mock_stream.call_args_list[-1])

    @pytest.mark.asyncio
    async def test_terminate_syncs_via_s3_on_aws(self, adapter, mock_k8s_clients):
        """On AWS the stop-path output persistence runs aws s3 (not gsutil) in the
        gcs-sync sidecar, and the output listing is done cloud-neutrally via the
        storage adapter (not an in-pod gsutil ls). Previously outputs silently did
        not persist on AWS (the sidecar's amazon/aws-cli image has no gsutil)."""
        mock_core, _ = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)

        s3 = MagicMock()
        s3.cli_auth_command.return_value = ""  # ambient (IRSA) on S3
        s3.build_uri.side_effect = lambda b, k: f"s3://{b}/{k}"
        s3.sync_out_command.side_effect = lambda d, prefix: ["/bin/sh", "-c", f"aws s3 sync {d} {prefix}"]
        s3.cli_copy_out_file.side_effect = lambda local, uri: f"aws s3 cp {local} {uri}"
        s3.list_objects = AsyncMock(return_value=[])

        with (
            patch("app.adapters.registry.get_storage_adapter", return_value=s3),
            patch("kubernetes.stream.stream") as mock_stream,
        ):
            await adapter._k8s_terminate_session(
                session_id=42,
                pod_name="bioaf-notebook-42",
                namespace="bioaf-notebooks",
                working_bucket="bioaf-working-5f6286",
                gcs_home_prefix="s3://bioaf-working-5f6286/notebooks/7/",
            )

        all_calls = str(mock_stream.call_args_list)
        assert "aws s3" in all_calls
        assert "gsutil" not in all_calls
        assert "gs://" not in all_calls
        # Output listing is cloud-neutral (storage adapter), not an in-pod gsutil ls.
        s3.list_objects.assert_awaited()

    @pytest.mark.asyncio
    async def test_terminate_deletes_pod(self, adapter, mock_k8s_clients):
        """Test 8: terminate deletes Pod and Service."""
        mock_core, _ = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)

        with patch("kubernetes.stream.stream"):
            await adapter._k8s_terminate_session(
                session_id=42,
                pod_name="bioaf-notebook-42",
                namespace="bioaf-notebooks",
                gcs_home_prefix="gs://bucket/notebooks/7/",
            )

        mock_core.delete_namespaced_pod.assert_called_once()
        mock_core.delete_namespaced_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_terminate_returns_stopped(self, adapter, mock_k8s_clients):
        """Test 9: terminate returns stopped status."""
        mock_core, _ = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)

        with patch("kubernetes.stream.stream"):
            result = await adapter._k8s_terminate_session(
                session_id=42,
                pod_name="bioaf-notebook-42",
                namespace="bioaf-notebooks",
                gcs_home_prefix="gs://bucket/notebooks/7/",
            )

        assert result["status"] == "stopped"
        assert "stopped_at" in result


class TestGetSessionStatus:
    @pytest.mark.asyncio
    async def test_running_status(self, adapter, mock_k8s_clients):
        """Test 10: running pod returns running status with access URL."""
        mock_core, _ = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)

        result = await adapter._k8s_get_session_status(
            session_id=42,
            pod_name="bioaf-notebook-42",
            namespace="bioaf-notebooks",
        )

        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_error_status(self, adapter, mock_k8s_clients):
        """Test 11: failed pod returns error status."""
        mock_core, _ = mock_k8s_clients
        mock_pod = MagicMock()
        mock_pod.status.phase = "Failed"
        mock_pod.status.conditions = []
        mock_core.read_namespaced_pod.return_value = mock_pod
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)

        result = await adapter._k8s_get_session_status(
            session_id=42,
            pod_name="bioaf-notebook-42",
            namespace="bioaf-notebooks",
        )

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_running_resolves_loadbalancer_access_url(self, adapter, mock_k8s_clients):
        """A live session resolves its LoadBalancer URL so the status endpoint can
        read access_url off the normalized SessionStatus (Phase 5)."""
        mock_core, _ = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._resolve_service_url = MagicMock(return_value="http://1.2.3.4:8888")

        result = await adapter._k8s_get_session_status(
            session_id=42,
            pod_name="bioaf-notebook-42",
            namespace="bioaf-notebooks",
            session_type="jupyter",
        )

        assert result["status"] == "running"
        assert result["access_url"] == "http://1.2.3.4:8888"
        adapter._resolve_service_url.assert_called_once_with("bioaf-notebook-svc-42", "bioaf-notebooks", 8888)

    @pytest.mark.asyncio
    async def test_rstudio_resolves_port_8787(self, adapter, mock_k8s_clients):
        """RStudio sessions resolve the URL on the RStudio port."""
        mock_core, _ = mock_k8s_clients
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._resolve_service_url = MagicMock(return_value="http://1.2.3.4:8787")

        result = await adapter._k8s_get_session_status(
            session_id=9,
            pod_name="bioaf-notebook-9",
            namespace="bioaf-notebooks",
            session_type="rstudio",
        )

        adapter._resolve_service_url.assert_called_once_with("bioaf-notebook-svc-9", "bioaf-notebooks", 8787)
        assert result["access_url"] == "http://1.2.3.4:8787"

    @pytest.mark.asyncio
    async def test_error_status_skips_url_resolution(self, adapter, mock_k8s_clients):
        """A failed pod resolves no URL (no live service)."""
        mock_core, _ = mock_k8s_clients
        mock_pod = MagicMock()
        mock_pod.status.phase = "Failed"
        mock_pod.status.conditions = []
        mock_core.read_namespaced_pod.return_value = mock_pod
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._resolve_service_url = MagicMock(return_value="http://nope:8888")

        result = await adapter._k8s_get_session_status(
            session_id=42, pod_name="bioaf-notebook-42", namespace="bioaf-notebooks", session_type="jupyter"
        )

        assert result["status"] == "error"
        adapter._resolve_service_url.assert_not_called()


class TestNamespaceSetup:
    @pytest.mark.asyncio
    async def test_namespace_created_on_first_launch(self, adapter, mock_k8s_clients):
        """Test 12: namespace and service account created on first launch."""
        mock_core, mock_rbac = mock_k8s_clients
        from kubernetes.client.rest import ApiException

        # Namespace does not exist
        mock_core.read_namespace.side_effect = ApiException(status=404)
        adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
        adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)

        await adapter.ensure_notebook_namespace()

        mock_core.create_namespace.assert_called_once()
        mock_core.create_namespaced_service_account.assert_called_once()


class TestOutOfClusterFallback:
    """Tests for out-of-cluster K8s client initialization."""

    @pytest.mark.asyncio
    async def test_incluster_config_used_when_available(self):
        """When running inside a K8s pod, incluster config is used."""
        provider = KubernetesNotebookProvider()
        provider._mode = "k8s"

        with (
            patch("app.adapters.kubernetes.connection.config") as mock_config,
            patch("app.adapters.kubernetes.connection.client") as mock_client,
        ):
            mock_config.load_incluster_config.return_value = None
            mock_api_client = MagicMock()
            mock_client.ApiClient.return_value = mock_api_client

            result = await provider._get_api_client_async()

            mock_config.load_incluster_config.assert_called_once()
            assert result == mock_api_client

    @pytest.mark.asyncio
    async def test_fallback_to_platform_config_when_not_in_cluster(self):
        """When not in a K8s pod, falls back to platform_config credentials."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("gke_cluster_endpoint", "https://1.2.3.4"),
            ("gke_cluster_ca_cert", "dGVzdA=="),  # base64("test")
            ("gcp_service_account_key", '{"type":"service_account","project_id":"test"}'),
        ]
        mock_session.execute.return_value = mock_result

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        provider = KubernetesNotebookProvider(session_factory=mock_session_factory)
        provider._mode = "k8s"

        with (
            patch("app.adapters.kubernetes.connection.config") as mock_config,
            patch("app.adapters.cluster_auth.gcp._get_gcp_token", return_value="fake-token"),
            patch("app.adapters.kubernetes.connection.tempfile") as mock_tempfile,
            patch("app.adapters.kubernetes.connection.client") as mock_client,
        ):
            mock_config.load_incluster_config.side_effect = Exception("not in cluster")
            mock_tmpfile = MagicMock()
            mock_tmpfile.name = "/tmp/fake-ca.crt"
            mock_tempfile.NamedTemporaryFile.return_value = mock_tmpfile
            mock_api_client = MagicMock()
            mock_client.ApiClient.return_value = mock_api_client
            mock_client.Configuration.return_value = MagicMock()

            result = await provider._get_api_client_async()

            mock_config.load_incluster_config.assert_called_once()
            assert result == mock_api_client

    @pytest.mark.asyncio
    async def test_raises_when_no_cluster_endpoint(self):
        """Raises RuntimeError when no cluster endpoint is configured."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        provider = KubernetesNotebookProvider(session_factory=mock_session_factory)
        provider._mode = "k8s"

        with patch("app.adapters.kubernetes.connection.config") as mock_config:
            mock_config.load_incluster_config.side_effect = Exception("not in cluster")

            # Stage 4a genericized this message ("cluster endpoint", was "GKE
            # cluster endpoint") when the auth provider was extracted.
            with pytest.raises(RuntimeError, match="No cluster endpoint"):
                await provider._get_api_client_async()

    @pytest.mark.asyncio
    async def test_cached_client_reused(self):
        """Cached API client is reused on subsequent calls."""
        provider = KubernetesNotebookProvider()
        provider._mode = "k8s"
        mock_client = MagicMock()
        provider._api_client = mock_client
        provider._client_created_at = 1.0  # recent enough

        with patch("app.adapters.kubernetes.connection.time") as mock_time:
            mock_time.monotonic.return_value = 100.0  # well within TTL

            result = await provider._get_api_client_async()

        assert result == mock_client

    def test_core_client_uses_api_client(self):
        """_get_k8s_core_client passes the shared ApiClient."""
        provider = KubernetesNotebookProvider()
        provider._mode = "k8s"
        mock_api_client = MagicMock()
        provider._api_client = mock_api_client
        provider._client_created_at = 1.0

        with (
            patch("app.adapters.kubernetes.connection.time") as mock_time,
            patch("app.adapters.kubernetes.connection.client") as mock_k8s,
        ):
            mock_time.monotonic.return_value = 100.0
            provider._get_k8s_core_client()

            mock_k8s.CoreV1Api.assert_called_once_with(api_client=mock_api_client)

    def test_rbac_client_uses_api_client(self):
        """_get_k8s_rbac_client passes the shared ApiClient."""
        provider = KubernetesNotebookProvider()
        provider._mode = "k8s"
        mock_api_client = MagicMock()
        provider._api_client = mock_api_client
        provider._client_created_at = 1.0

        with (
            patch("app.adapters.kubernetes.connection.time") as mock_time,
            patch("app.adapters.kubernetes.connection.client") as mock_k8s,
        ):
            mock_time.monotonic.return_value = 100.0
            provider._get_k8s_rbac_client()

            mock_k8s.RbacAuthorizationV1Api.assert_called_once_with(api_client=mock_api_client)
