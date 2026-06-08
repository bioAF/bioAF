"""Secrets provider factory (Phase 9C).

Selected by bootstrap config (default GCP), not the DB-backed registry, because
secrets are fetched before the database is reachable.
"""

from __future__ import annotations

from app.adapters.secrets.base import SecretsProvider

VALID_SECRETS_BACKENDS = ("gcp",)
DEFAULT_SECRETS_BACKEND = "gcp"


def create_secrets_provider(project_id: str, backend: str = DEFAULT_SECRETS_BACKEND) -> SecretsProvider:
    """Instantiate the secrets provider for ``backend`` (default GCP)."""
    if backend not in VALID_SECRETS_BACKENDS:
        raise ValueError(f"Unknown secrets backend '{backend}'. Valid options: {VALID_SECRETS_BACKENDS}")
    from app.adapters.secrets.gcp import GcpSecretsProvider

    return GcpSecretsProvider(project_id)
