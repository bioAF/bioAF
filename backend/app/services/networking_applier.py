"""Applier abstraction for networking operations that touch the cluster.

The networking settings API talks to the cluster for three things:

- Request a TLS certificate for the configured FQDN.
- Read the current certificate status.
- Enforce HTTPS at the Ingress and trigger a rollout restart.

Production uses :class:`KubernetesNetworkingApplier` which patches the GKE
``ManagedCertificate`` resource, the Ingress, and the Deployment templates.
Tests and local-mode dev use :class:`MockNetworkingApplier`, which records
calls in memory and returns deterministic status values.

The selector :func:`get_networking_applier` is a FastAPI dependency. Tests
override it via ``app.dependency_overrides``; production reads the
``BIOAF_COMPUTE_MODE`` setting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol


CERT_STATUS_NOT_REQUESTED = "not_requested"
CERT_STATUS_PROVISIONING = "provisioning"
CERT_STATUS_ACTIVE = "active"
CERT_STATUS_FAILED = "failed"


class NetworkingApplier(Protocol):
    async def request_certificate(self, fqdn: str) -> None: ...
    async def get_certificate_status(self, fqdn: str) -> str: ...
    async def enforce_https(self, fqdn: str, enabled: bool) -> None: ...
    async def restart_services(self) -> None: ...


@dataclass
class MockNetworkingApplier:
    """In-memory applier for tests and BIOAF_COMPUTE_MODE=local.

    A test (or local-dev session) can read ``requested_for`` to confirm
    which FQDN was sent to the cluster, and set ``status_to_return`` to
    drive the next ``get_certificate_status`` call.
    """

    requested_for: str | None = None
    status_to_return: str = CERT_STATUS_PROVISIONING
    enforce_calls: list[tuple[str, bool]] = field(default_factory=list)
    restart_count: int = 0

    async def request_certificate(self, fqdn: str) -> None:
        self.requested_for = fqdn

    async def get_certificate_status(self, fqdn: str) -> str:
        return self.status_to_return

    async def enforce_https(self, fqdn: str, enabled: bool) -> None:
        self.enforce_calls.append((fqdn, enabled))

    async def restart_services(self) -> None:
        self.restart_count += 1


class KubernetesNetworkingApplier:
    """Production applier: patches GKE ManagedCertificate, Ingress, Deployments.

    Implementation is intentionally minimal in this commit: it raises
    NotImplementedError until the K8s API wiring lands. The applier interface
    exists so the API layer is testable in isolation today; the real K8s
    calls follow once the helm-side RBAC + manifests are in place.
    """

    async def request_certificate(self, fqdn: str) -> None:
        raise NotImplementedError("KubernetesNetworkingApplier.request_certificate is not yet wired up")

    async def get_certificate_status(self, fqdn: str) -> str:
        raise NotImplementedError("KubernetesNetworkingApplier.get_certificate_status is not yet wired up")

    async def enforce_https(self, fqdn: str, enabled: bool) -> None:
        raise NotImplementedError("KubernetesNetworkingApplier.enforce_https is not yet wired up")

    async def restart_services(self) -> None:
        raise NotImplementedError("KubernetesNetworkingApplier.restart_services is not yet wired up")


# Module-level singleton so the same mock state survives across requests
# within a test. Tests typically override this via FastAPI dependency
# overrides, but local-mode dev wants a stable instance.
_mock_singleton = MockNetworkingApplier()


def get_networking_applier() -> NetworkingApplier:
    """FastAPI dependency: pick the applier based on compute mode."""
    mode = os.environ.get("BIOAF_COMPUTE_MODE", "kubernetes")
    if mode == "kubernetes":
        return KubernetesNetworkingApplier()
    return _mock_singleton


def reset_mock_applier() -> MockNetworkingApplier:
    """Reset the shared mock state between tests that share the singleton."""
    global _mock_singleton
    _mock_singleton = MockNetworkingApplier()
    return _mock_singleton
