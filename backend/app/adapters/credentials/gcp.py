"""GcpCredentialsProvider: the GCP realization of the Credentials seam (Stage 3c).

Forwards credential resolution to ``app.platform.credential_injector`` (the
existing, tested GCP implementation) so the service-layer credential leaks can
drain onto this seam without first relocating the injector: every mock that
patches ``credential_injector.load_gcp_credentials`` keeps working through this
provider. The injector folds into this class in the final 3c block, when it is
removed from ``platform/`` and its remaining callers re-point here.

The two shapes the injector did not expose are implemented here directly (GCP
credential SDK imports are allowed inside ``adapters/``): ``bearer_token`` (mint a
fresh REST access token) and ``is_permission_denied`` (classify a Forbidden).
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import google.auth.transport.requests as _ga_transport
from google.api_core import exceptions as _gapi_exceptions

from app.adapters.credentials.base import USE_DEFAULT_IMPERSONATION, CredentialsProvider
from app.platform import credential_injector


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
