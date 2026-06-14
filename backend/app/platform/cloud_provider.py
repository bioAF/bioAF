"""Cloud provider identity and backend-resolution policy (the keystone).

bioAF runs on exactly one cloud per install. ``cloud_provider`` is a single
``platform_config`` key (``gcp`` | ``aws``), set once at install and immutable
thereafter, that is the sole answer to "which cloud is this?". Every BAL seam
resolves its backend from it, so the rest of the multi-platform design hangs on
this module.

Resolution rule for a seam:

    resolve_backend(seam) = per_seam_override ?? POLICY[cloud_provider][seam]

A per-seam override (``platform_config.<seam>_backend``) wins for testing / mixed
setups, BUT resolution FAILS CLOSED: an override naming a backend that is invalid
for ``(cloud_provider, seam)`` (e.g. ``storage_backend=s3`` on a ``gcp`` install)
raises rather than being honored. The backend, not just the frontend, is the
source of truth for valid combos, so a bad prefill/API config cannot half-break a
running install. The stage-8 ``/stack-options`` endpoint reads this same policy.

Backward compatibility: existing GCP installs have no ``cloud_provider`` row, so
the unset default is ``gcp`` and the ``gcp`` policy row equals each seam's current
hard-default backend. Resolving from the policy therefore changes no behavior on a
GCP install (it returns exactly what the factories pick today).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.platform_config_service import PlatformConfigService

# The single authoritative platform_config key for cloud identity.
CLOUD_PROVIDER_KEY = "cloud_provider"

# Default when the key is unset: existing GCP installs and local dev have no
# cloud_provider row and must keep behaving as GCP.
DEFAULT_CLOUD_PROVIDER = "gcp"

# cloud_provider -> {seam: default backend}. These are the substrate seams whose
# backend is determined purely by the cloud, plus storage (which stage 2 decouples
# from compute_stack so it too resolves from the cloud). The user-selectable
# compute backend (kubernetes / slurm) is a workload choice, not cloud-derived, so
# it is intentionally absent here. The gcp row must mirror each seam's current
# hard-default (see the module docstring's backward-compatibility note).
POLICY: dict[str, dict[str, str]] = {
    "gcp": {
        "storage": "gcs",
        "work_node": "gce",
        "iam": "gcp",
        "billing": "gcp",
        "messaging": "gcp",
        "secrets": "gcp",
        "log_sink": "gcp",
        "credentials": "gcp",
        "cluster_auth": "gke",
        "pod_identity": "gke",
        "image_registry": "artifact_registry",
        "image_build": "cloud_build",
    },
    "aws": {
        "storage": "s3",
        "work_node": "ec2",
        "iam": "aws",
        "billing": "aws",
        "messaging": "aws",
        "secrets": "aws",
        "log_sink": "aws",
        "credentials": "aws",
        "cluster_auth": "eks",
        "pod_identity": "eks",
        "image_registry": "ecr",
        "image_build": "codebuild",
    },
}

# Backends valid on ANY cloud for a seam, because the user selects them via the
# workload choice rather than the cloud. SLURM compute stages on NFS regardless of
# cloud, so nfs is a valid storage backend on every cloud (in addition to the
# cloud's object-store default).
CLOUD_AGNOSTIC_BACKENDS: dict[str, frozenset[str]] = {
    "storage": frozenset({"nfs"}),
}

# The seams the policy governs. Both cloud rows define the same set.
SEAMS: frozenset[str] = frozenset(POLICY[DEFAULT_CLOUD_PROVIDER])

# The clouds the policy supports today. on_prem is likely-future, out of scope.
SUPPORTED_CLOUD_PROVIDERS: tuple[str, ...] = tuple(POLICY)


class InvalidBackendError(ValueError):
    """A ``(cloud_provider, seam, backend)`` combination the policy disallows.

    Raised on an unknown cloud/seam or an explicit override that names a backend
    not valid for the install's cloud, so a bad config fails closed instead of
    half-breaking a running install.
    """


def _require_cloud(cloud_provider: str) -> None:
    if cloud_provider not in POLICY:
        raise InvalidBackendError(f"Unknown cloud_provider '{cloud_provider}'. Supported: {sorted(POLICY)}.")


def _require_seam(seam: str) -> None:
    if seam not in SEAMS:
        raise InvalidBackendError(f"Unknown seam '{seam}'. Known seams: {sorted(SEAMS)}.")


def valid_backends(cloud_provider: str, seam: str) -> frozenset[str]:
    """The backends valid for ``seam`` on ``cloud_provider`` (default + agnostic)."""
    _require_cloud(cloud_provider)
    _require_seam(seam)
    return frozenset({POLICY[cloud_provider][seam]}) | CLOUD_AGNOSTIC_BACKENDS.get(seam, frozenset())


def is_valid_combo(cloud_provider: str, seam: str, backend: str) -> bool:
    """True if ``backend`` is a valid choice for ``seam`` on ``cloud_provider``."""
    return backend in valid_backends(cloud_provider, seam)


def resolve(cloud_provider: str, seam: str, override: str | None = None) -> str:
    """Pure resolution: a valid per-seam override wins, else the policy default.

    Fails closed: an override naming a backend invalid for
    ``(cloud_provider, seam)`` raises ``InvalidBackendError``. An empty or ``None``
    override is treated as unset.
    """
    _require_cloud(cloud_provider)
    _require_seam(seam)
    if override:
        if not is_valid_combo(cloud_provider, seam, override):
            raise InvalidBackendError(
                f"Backend '{override}' is not valid for seam '{seam}' on a "
                f"'{cloud_provider}' install. Valid: {sorted(valid_backends(cloud_provider, seam))}."
            )
        return override
    return POLICY[cloud_provider][seam]


async def get_cloud_provider(session: AsyncSession) -> str:
    """Read the install's cloud_provider from platform_config (default gcp)."""
    value = await PlatformConfigService.get(session, CLOUD_PROVIDER_KEY)
    return value or DEFAULT_CLOUD_PROVIDER


async def resolve_backend(session: AsyncSession, seam: str) -> str:
    """Resolve the active backend for ``seam`` from platform_config.

    Reads ``cloud_provider`` (default gcp) and the optional per-seam override
    ``<seam>_backend``, then applies the pure policy. Behavior-preserving on a GCP
    install: returns each seam's current hard-default backend.
    """
    cfg = await PlatformConfigService.get_many(session, [CLOUD_PROVIDER_KEY, f"{seam}_backend"])
    cloud_provider = cfg.get(CLOUD_PROVIDER_KEY) or DEFAULT_CLOUD_PROVIDER
    override = cfg.get(f"{seam}_backend")
    return resolve(cloud_provider, seam, override)


# --- Resolved-backend cache (for sessionless call sites) ----------------------
#
# cloud_provider is immutable for the life of the install, so each seam's backend
# can be resolved once at startup and cached. The registry loads this cache during
# adapter init (where a DB session exists, after cloud_provider is persisted); the
# factory call sites that have no session in scope (the secrets provider is built
# pre-DB; the billing / iam / messaging providers are created on-demand with only
# credentials available) then read their backend synchronously via backend_for.
_resolved_backends: dict[str, str] = {}


async def load_resolved_backends(session: AsyncSession) -> None:
    """Resolve and cache every seam's backend (called once at adapter init).

    Reads ``cloud_provider`` and all per-seam overrides in one query, then applies
    the pure policy per seam. An invalid override raises here, so a bad config
    fails closed at startup rather than at first use.
    """
    global _resolved_backends
    keys = [CLOUD_PROVIDER_KEY] + [f"{seam}_backend" for seam in SEAMS]
    cfg = await PlatformConfigService.get_many(session, keys)
    cloud_provider = cfg.get(CLOUD_PROVIDER_KEY) or DEFAULT_CLOUD_PROVIDER
    _resolved_backends = {seam: resolve(cloud_provider, seam, cfg.get(f"{seam}_backend")) for seam in SEAMS}


def backend_for(seam: str) -> str:
    """Synchronously read a seam's resolved backend (cached at startup).

    Falls back to the gcp policy default when the cache is unloaded (pre-DB
    bootstrap, local dev, or tests that never call ``load_resolved_backends``), so
    call sites behave exactly as before on GCP.
    """
    _require_seam(seam)
    return _resolved_backends.get(seam, POLICY[DEFAULT_CLOUD_PROVIDER][seam])


def reset_resolved_backends() -> None:
    """Clear the resolved-backend cache (registry reset / test isolation)."""
    global _resolved_backends
    _resolved_backends = {}
