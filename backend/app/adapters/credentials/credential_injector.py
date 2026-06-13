"""GCP credential helpers.

Provides:
- ``GCPCredentialInjector``: builds subprocess env vars for Terraform
- ``load_gcp_credentials``: returns a google-auth Credentials object for
  use with Python GCP client libraries (BigQuery, Storage, etc.)

Supports two credential sources:
- vm_default: uses the VM's attached service account via ADC
- service_account_key: writes the JSON key to a temp file and sets
  GOOGLE_APPLICATION_CREDENTIALS
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import google.auth as _google_auth
from google.auth import impersonated_credentials as _impersonated_credentials
from google.oauth2 import service_account

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

_GCP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Sentinel for load_gcp_credentials(impersonate_target=...). It distinguishes
# three states the credential seam needs: derive the target from config (the
# default, used by almost every caller), an explicit None (raw ADC, no
# impersonation: the gcp_config app-credentials probe), and an explicit SA email
# (impersonate a named SA: the gcp_config bootstrap probe, the Sheets reader SA).
_USE_CONFIG_TARGET: Any = object()


def _impersonation_target(config: dict[str, Any]) -> str:
    """Pick the SA email to impersonate in vm_default mode.

    Prefers the new gcp_bootstrap_sa_email key; falls back to the legacy
    gcp_service_account_email for installs that pre-date SA hardening.
    """
    return config.get("gcp_bootstrap_sa_email") or config.get("gcp_service_account_email") or ""


def load_gcp_credentials(
    config: dict[str, Any],
    *,
    scopes: list[str] | None = None,
    impersonate_target: str | None = _USE_CONFIG_TARGET,
    key_json: str | None = None,
    lifetime: int | None = None,
) -> "Credentials":
    """Load GCP credentials from a platform_config dict.

    Returns a Credentials object suitable for passing to any GCP Python client
    (BigQuery, Storage, etc.). Called with no keyword arguments it is byte-for-byte
    the prior behavior (full cloud-platform scope, config-derived impersonation);
    the keywords let the credential seam serve the few callers that need a narrower
    scope, an explicit impersonation target, a different key, or a token lifetime:

    - ``scopes``: OAuth scopes (default cloud-platform).
    - ``impersonate_target``: ``_USE_CONFIG_TARGET`` (derive from config, the
      default), ``None`` (raw ADC, no impersonation), or an explicit SA email.
    - ``key_json``: override the service-account-key JSON (else
      ``gcp_service_account_key`` from config).
    - ``lifetime``: impersonated-token lifetime in seconds (else the library
      default).
    """
    scopes = scopes or _GCP_SCOPES
    credential_source = config.get("gcp_credential_source", "vm_default")

    if credential_source == "service_account_key":
        key = key_json if key_json is not None else config.get("gcp_service_account_key", "")
        key_data = json.loads(key)
        return service_account.Credentials.from_service_account_info(key_data, scopes=scopes)

    # vm_default: use ADC, optionally impersonating a target SA.
    target = _impersonation_target(config) if impersonate_target is _USE_CONFIG_TARGET else impersonate_target
    if target:
        # The source identity needs full cloud-platform scope to mint the
        # impersonated token (the iamcredentials generateAccessToken call); the
        # minted token itself then carries the requested ``scopes``.
        source_creds, _ = _google_auth.default(scopes=_GCP_SCOPES)
        kwargs: dict[str, Any] = {
            "source_credentials": source_creds,
            "target_principal": target,
            "target_scopes": scopes,
        }
        if lifetime is not None:
            kwargs["lifetime"] = lifetime
        return _impersonated_credentials.Credentials(**kwargs)
    source_creds, _ = _google_auth.default(scopes=scopes)
    return source_creds


class GCPCredentialInjector:
    """Build subprocess environment variables for Terraform GCP operations."""

    @staticmethod
    async def build_env(config: dict) -> tuple[dict, Callable[[], Coroutine]]:
        """Build env dict and cleanup callable from platform_config values.

        Args:
            config: Dict containing GCP config keys such as those from
                    platform_config: gcp_credential_source, gcp_project_id,
                    gcp_region, gcp_zone, gcp_service_account_key.

        Returns:
            (env, cleanup) where env is a dict of environment variables to
            merge into the subprocess environment, and cleanup is an async
            callable that removes temporary files.

        Raises:
            ValueError: If credential_source is service_account_key but no key
                        JSON is present in config.
        """
        credential_source = config.get("gcp_credential_source", "vm_default")
        project_id = config.get("gcp_project_id", "")
        region = config.get("gcp_region", "us-central1")
        zone = config.get("gcp_zone", "us-central1-a")

        env: dict[str, str] = {
            "TF_VAR_project_id": project_id,
            "TF_VAR_region": region,
            "TF_VAR_zone": zone,
        }

        if credential_source == "service_account_key":
            sa_key = config.get("gcp_service_account_key")
            if not sa_key:
                raise ValueError(
                    "gcp_credential_source is 'service_account_key' but no service_account_key value found in config"
                )

            # Write key to a named temp file
            fd, key_path = tempfile.mkstemp(suffix=".json", prefix="bioaf_sa_")
            try:
                os.write(fd, sa_key.encode())
            finally:
                os.close(fd)

            env["GOOGLE_APPLICATION_CREDENTIALS"] = key_path

            async def _cleanup_sa() -> None:
                p = Path(key_path)
                if p.exists():
                    p.unlink()

            return env, _cleanup_sa

        else:
            # vm_default: ADC picks up the VM's attached SA (bioaf-app)
            # automatically. If a bootstrap impersonation target is
            # configured, ask the Google Terraform provider to impersonate
            # it via the standard env var; without this, `terraform apply`
            # runs under bioaf-app's narrow scoped permissions and fails on
            # any operation that needs the broad bootstrap grants
            # (pubsub.topics.create, iam.serviceAccounts.create,
            # cloudbuild.builds.create, etc.).
            target = _impersonation_target(config)
            if target:
                env["GOOGLE_IMPERSONATE_SERVICE_ACCOUNT"] = target

            async def _noop_cleanup() -> None:
                pass

            return env, _noop_cleanup
