"""CredentialsProvider: the BAL seam for resolving cloud credentials (Stage 3c).

A platform-substrate provider (Tier B in the target architecture): how the app
authenticates to its cloud. GCP resolves Application Default Credentials, SA-key
JSON, and SA impersonation; AWS would resolve STS / assume-role / instance
profile. The service layer must name no cloud, so every credential shape the
services need is exposed here as a backend-neutral method:

- ``load_credentials``: a credentials object to hand to a cloud client library.
- ``bearer_token``: a fresh access-token string for a REST Authorization header.
- ``build_subprocess_env``: env vars (plus cleanup) for a subprocess such as
  Terraform.
- ``is_permission_denied``: classify a cloud exception as access-denied without
  the caller importing the cloud SDK's exception type.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine

# Sentinel for load_credentials(impersonate_target=...): "derive the target from
# config" (the default), as distinct from an explicit None (no impersonation) or
# an explicit SA email. Lives on the seam so callers never touch the GCP-internal
# sentinel.
USE_DEFAULT_IMPERSONATION: Any = object()


class CredentialsProvider(ABC):
    """Resolve cloud credentials for the app in the shapes the service layer needs."""

    @abstractmethod
    def load_credentials(
        self,
        config: dict[str, Any],
        *,
        scopes: list[str] | None = None,
        impersonate_target: Any = USE_DEFAULT_IMPERSONATION,
        key_json: str | None = None,
        lifetime: int | None = None,
    ) -> Any:
        """Return a credentials object for a cloud client library.

        ``impersonate_target`` defaults to ``USE_DEFAULT_IMPERSONATION`` (derive
        from config); pass ``None`` for no impersonation or an explicit principal
        to impersonate a named identity. ``scopes`` / ``key_json`` / ``lifetime``
        override the defaults for the few callers that need them.
        """

    @abstractmethod
    def bearer_token(self, credentials: Any) -> str:
        """Refresh ``credentials`` and return a fresh access-token string.

        For callers that hit a cloud REST API directly and need a
        ``Authorization: Bearer <token>`` header.
        """

    @abstractmethod
    async def build_subprocess_env(
        self, config: dict[str, Any]
    ) -> tuple[dict[str, str], Callable[[], Coroutine[Any, Any, None]]]:
        """Build ``(env, cleanup)`` for a credentialed subprocess (e.g. Terraform).

        ``env`` is merged into the subprocess environment; ``cleanup`` is an async
        callable that removes any temporary files the env references.
        """

    @abstractmethod
    def is_permission_denied(self, exc: BaseException) -> bool:
        """True if ``exc`` is the backend's access-denied error.

        Lets a caller branch on permission-denied without importing the cloud
        SDK's exception type.
        """
