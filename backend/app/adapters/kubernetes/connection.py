"""Shared GKE connection + auth collaborator.

`GkeConnection` owns the Kubernetes connection state and the plumbing that was
previously copy-pasted into the compute, notebook, and cellxgene providers:
reading cluster config from platform_config, building an out-of-cluster API
client with a GCP bearer token, token-expiry tracking, and the cached API
client. Per-provider differences (which config keys to read, whether to
invalidate the cached client on a forced reload, and how aggressively to
refresh) are passed in at construction so each provider keeps its exact
current behavior while sharing one implementation.
"""

import base64
import logging
import tempfile
import time

from kubernetes import client, config

logger = logging.getLogger(__name__)

# First-time client setup for kubectl access to a GKE cluster. Lives in the K8s
# adapter package (cloud CLI strings are allowed here); the EKS realization
# (Stage 6e) returns the `aws eks update-kubeconfig` form behind the same seam.
KUBECTL_SETUP_GUIDE = """First-time setup for kubectl access:

1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
2. Authenticate: gcloud auth login
3. Get cluster credentials:
   gcloud container clusters get-credentials bioaf-cluster --region <region> --project <project-id>
4. Verify access: kubectl get pods -n bioaf-pipelines"""


class GkeConnection:
    """Managed-Kubernetes connection + auth shared by the K8s BAL providers.

    Cloud-neutral plumbing (CA-to-tempfile, ``Configuration`` assembly,
    in-cluster-first fallback, cached-client refresh). The cloud-specific bits -
    the control-plane endpoint, CA cert, bearer token, and refresh TTL - come
    from a ``ClusterAuthProvider`` resolved from ``cloud_provider`` (Stage 4a).
    """

    def __init__(
        self,
        *,
        config_keys: list[str],
        session_factory=None,
        invalidate_client_on_force: bool = True,
        refresh_strategy: str = "simple",
    ):
        self._config_keys = config_keys
        self._session_factory = session_factory
        self._invalidate_client_on_force = invalidate_client_on_force
        self._refresh_strategy = refresh_strategy
        self._api_client: client.ApiClient | None = None
        self._client_created_at: float = 0.0
        self._cluster_config: dict | None = None
        # (endpoint, ca_cert) identity the cached client was built for; used by
        # the fingerprint refresh strategy to rebuild when the cluster changes.
        self._cached_cluster_fingerprint: tuple[str, str] = ("", "")
        self._cluster_auth_provider = None

    @property
    def _cluster_auth(self):
        """The cloud-resolved ClusterAuthProvider (lazy; GKE by default)."""
        if self._cluster_auth_provider is None:
            from app.adapters.cluster_auth import get_cluster_auth_provider

            self._cluster_auth_provider = get_cluster_auth_provider()
        return self._cluster_auth_provider

    async def load_cluster_config(self, force: bool = False) -> dict:
        """Read GKE cluster config from platform_config into _cluster_config.

        Must be awaited (e.g. during app startup) so the DB query runs on the
        correct event loop; the result is cached for later sync access. Re-reads
        when forced or when the cached endpoint is missing/null so a newly
        deployed cluster is picked up without a restart. When
        ``invalidate_client_on_force`` is set, a forced reload also drops the
        cached API client so it rebuilds against the fresh config.
        """
        if self._cluster_config is not None and not force:
            endpoint = self._cluster_auth.cluster_endpoint(self._cluster_config)
            if endpoint and endpoint != "null":
                return self._cluster_config

        if not self._session_factory:
            self._cluster_config = {}
            return self._cluster_config

        from app.platform.platform_config_service import PlatformConfigService

        async with self._session_factory() as session:
            # get_many decrypts gcp_service_account_key; the credential consumers
            # (credential_injector / kube client) expect the plaintext JSON.
            self._cluster_config = await PlatformConfigService.get_many(session, self._config_keys)

        if force and self._invalidate_client_on_force:
            self._api_client = None

        return self._cluster_config

    def is_token_expired(self) -> bool:
        """Check if the cached bearer token is older than the provider's TTL."""
        if self._client_created_at == 0.0:
            return False
        return (time.monotonic() - self._client_created_at) > self._cluster_auth.token_ttl_seconds

    def build_out_of_cluster_client(self) -> client.ApiClient:
        """Build a K8s ApiClient using platform_config credentials.

        Requires cluster config to have been loaded first so _cluster_config is
        populated.
        """
        cfg = self._cluster_config or {}

        endpoint = self._cluster_auth.cluster_endpoint(cfg)
        ca_cert_b64 = self._cluster_auth.cluster_ca_cert(cfg)

        if not endpoint or endpoint == "null":
            raise RuntimeError("No cluster endpoint in platform_config. Deploy the compute stack first.")

        if not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"

        token = self._cluster_auth.bearer_token(cfg)

        ca_cert_bytes = base64.b64decode(ca_cert_b64)
        ca_file = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
        ca_file.write(ca_cert_bytes)
        ca_file.close()

        configuration = client.Configuration()
        configuration.host = endpoint
        configuration.ssl_ca_cert = ca_file.name

        api_client = client.ApiClient(configuration)
        # The kubernetes-python client does not route Configuration.api_key into
        # request headers unless an OpenAPI security scheme references it. The
        # K8s OpenAPI spec the library ships with does not declare one for the
        # simple bearer-token case, so api_key is a silent no-op and every
        # request goes out anonymously, yielding 401 Unauthorized. Set the
        # header on the client directly instead.
        api_client.set_default_header("Authorization", f"Bearer {token}")

        self._client_created_at = time.monotonic()
        return api_client

    def get_api_client(self) -> client.ApiClient:
        """Get or create a K8s ApiClient, trying incluster first (sync).

        Uses the cached client when present and the token is not near expiry;
        does not reload cluster config from the DB.
        """
        if self._api_client is not None and not self.is_token_expired():
            return self._api_client

        if self.is_token_expired():
            logger.info("GCP access token approaching expiry, refreshing K8s client")
            self._api_client = None

        try:
            config.load_incluster_config()
            self._api_client = client.ApiClient()
            logger.info("Using incluster K8s config")
        except Exception:
            logger.info("Not running in cluster, using platform_config credentials")
            try:
                self._api_client = self.build_out_of_cluster_client()
                logger.info(
                    "K8s client built for endpoint %s",
                    self._cluster_auth.cluster_endpoint(self._cluster_config or {}),
                )
            except Exception:
                logger.exception("Failed to build out-of-cluster K8s client")
                raise

        return self._api_client

    def _cluster_fingerprint(self) -> tuple[str, str]:
        """(endpoint, ca_cert) identity of the cluster in the current config."""
        cfg = self._cluster_config or {}
        return (
            self._cluster_auth.cluster_endpoint(cfg),
            self._cluster_auth.cluster_ca_cert(cfg),
        )

    async def get_api_client_async(self) -> client.ApiClient:
        """Get or create a K8s ApiClient, trying incluster first (async).

        Dispatches on the configured refresh strategy. ``simple`` reloads
        cluster config only in the out-of-cluster fallback branch; ``fingerprint``
        reloads on every call and rebuilds the client when the cluster identity
        changed (e.g. a teardown + redeploy), not just when the token expires.
        """
        if self._refresh_strategy == "fingerprint":
            return await self._get_api_client_async_fingerprint()
        return await self._get_api_client_async_simple()

    async def _get_api_client_async_simple(self) -> client.ApiClient:
        if self._api_client is not None and not self.is_token_expired():
            return self._api_client

        if self.is_token_expired():
            logger.info("GCP access token approaching expiry, refreshing K8s client")
            self._api_client = None

        try:
            config.load_incluster_config()
            self._api_client = client.ApiClient()
            logger.info("Using incluster K8s config")
        except Exception:
            logger.info("Not running in cluster, using platform_config credentials")
            # Reload config in case it was stale at startup.
            await self.load_cluster_config(force=True)
            try:
                self._api_client = self.build_out_of_cluster_client()
                logger.info(
                    "K8s client built for endpoint %s",
                    self._cluster_auth.cluster_endpoint(self._cluster_config or {}),
                )
            except Exception:
                logger.exception("Failed to build out-of-cluster K8s client")
                raise

        return self._api_client

    async def _get_api_client_async_fingerprint(self) -> client.ApiClient:
        # Re-read platform_config every call so a cluster teardown + redeploy
        # (which rewrites endpoint and ca_cert) invalidates the cached client.
        await self.load_cluster_config(force=True)
        current_fp = self._cluster_fingerprint()

        cluster_changed = self._api_client is not None and self._cached_cluster_fingerprint != current_fp
        if self._api_client is not None and not self.is_token_expired() and not cluster_changed:
            return self._api_client

        if self.is_token_expired():
            logger.info("GCP access token approaching expiry, refreshing K8s client")
        elif cluster_changed:
            logger.info(
                "Cluster identity changed in platform_config, rebuilding K8s client (old endpoint=%s, new endpoint=%s)",
                self._cached_cluster_fingerprint[0],
                current_fp[0],
            )
        self._api_client = None

        try:
            config.load_incluster_config()
            self._api_client = client.ApiClient()
            logger.info("Using incluster K8s config")
        except Exception:
            logger.info("Not running in cluster, using platform_config credentials")
            try:
                self._api_client = self.build_out_of_cluster_client()
                self._cached_cluster_fingerprint = current_fp
                logger.info("K8s client built for endpoint %s", current_fp[0])
            except Exception:
                logger.exception("Failed to build out-of-cluster K8s client")
                raise

        return self._api_client

    def core_v1(self):
        """CoreV1Api bound to the shared API client."""
        return client.CoreV1Api(api_client=self.get_api_client())

    def batch_v1(self):
        """BatchV1Api bound to the shared API client."""
        return client.BatchV1Api(api_client=self.get_api_client())

    def rbac_v1(self):
        """RbacAuthorizationV1Api bound to the shared API client."""
        return client.RbacAuthorizationV1Api(api_client=self.get_api_client())

    def apps_v1(self):
        """AppsV1Api bound to the shared API client."""
        return client.AppsV1Api(api_client=self.get_api_client())
