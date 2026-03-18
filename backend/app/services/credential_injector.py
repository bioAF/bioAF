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


def load_gcp_credentials(config: dict[str, Any]) -> "Credentials":
    """Load GCP credentials from a platform_config dict.

    Returns a Credentials object with full cloud-platform scope, suitable
    for passing to any GCP Python client (BigQuery, Storage, etc.).
    """
    credential_source = config.get("gcp_credential_source", "vm_default")
    sa_email = config.get("gcp_service_account_email", "")

    if credential_source == "service_account_key":
        key_json = config.get("gcp_service_account_key", "")
        key_data = json.loads(key_json)
        return service_account.Credentials.from_service_account_info(key_data, scopes=_GCP_SCOPES)

    # vm_default: use ADC, optionally impersonating a target SA
    source_creds, _ = _google_auth.default(scopes=_GCP_SCOPES)
    if sa_email:
        return _impersonated_credentials.Credentials(
            source_credentials=source_creds,
            target_principal=sa_email,
            target_scopes=_GCP_SCOPES,
        )
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
            # vm_default: ADC picks up VM's service account automatically
            async def _noop_cleanup() -> None:
                pass

            return env, _noop_cleanup
