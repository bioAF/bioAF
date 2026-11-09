"""GcpCredentialsProvider: the GCP realization of the Credentials seam (Stage 3c).

Delegates credential resolution to the sibling ``credential_injector`` module (the
GCP implementation: ADC / SA-key / impersonation for ``load_credentials``, and the
Terraform subprocess env for ``build_subprocess_env``). Both live under
``adapters/`` now, so their google-auth imports are allowed and invisible to the
BAL guard. The two shapes the injector does not own are implemented here directly:
``bearer_token`` (mint a fresh REST access token), ``is_permission_denied``
(classify a Forbidden), and ``credential_failure_kind`` (classify a google-auth failure).
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import google.auth.transport.requests as _ga_transport
from google.auth import exceptions as _ga_exceptions
from google.api_core import exceptions as _gapi_exceptions

from app.adapters.credentials import credential_injector
from app.adapters.credentials.base import USE_DEFAULT_IMPERSONATION, CredentialsProvider


class GcpCredentialsProvider(CredentialsProvider):
    """Resolve GCP credentials (ADC / SA key / impersonation) for the app."""

    def load_credentials(
        self,
        config: dict[str, Any],
        *,
        scopes: list[str] | None = None,
        impersonate_target: Any = USE_DEFAULT_IMPERSONATION,
        key_json: str | None = None,
        lifetime: int | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"scopes": scopes, "key_json": key_json, "lifetime": lifetime}
        # Only forward an explicit target; the default lets the injector derive it
        # from config (its own sentinel), keeping the standard path byte-identical.
        if impersonate_target is not USE_DEFAULT_IMPERSONATION:
            kwargs["impersonate_target"] = impersonate_target
        return credential_injector.load_gcp_credentials(config, **kwargs)

    def bearer_token(self, credentials: Any) -> str:
        credentials.refresh(_ga_transport.Request())
        return credentials.token

    async def build_subprocess_env(
        self, config: dict[str, Any]
    ) -> tuple[dict[str, str], Callable[[], Coroutine[Any, Any, None]]]:
        return await credential_injector.GCPCredentialInjector.build_env(config)

    def is_permission_denied(self, exc: BaseException) -> bool:
        return isinstance(exc, _gapi_exceptions.Forbidden)

    def credential_failure_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, _ga_exceptions.TimeoutError):
            return "timeout"
        if isinstance(exc, _ga_exceptions.TransportError):
            return "transport"
        if isinstance(exc, (_ga_exceptions.RefreshError, _ga_exceptions.DefaultCredentialsError)):
            return "auth"
        return None
