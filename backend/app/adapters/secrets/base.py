"""SecretsProvider: the BAL seam for a managed secrets store (Phase 9C).

A platform-service provider (distinct from the five runtime adapter categories in
app/adapters/base.py): secrets are fetched at startup, before the DB and the
adapter registry are up, so this provider is selected by bootstrap config rather
than resolved from platform_config. GCP wraps Secret Manager; AWS would wrap
Secrets Manager; on-prem could wrap Vault or a file-backed store.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SecretsProvider(ABC):
    """Read managed secret values from the backend's secret store."""

    @abstractmethod
    def access_secret(self, name: str) -> str:
        """Return the latest version of secret ``name`` as text.

        Raises on failure (missing secret, no access); callers that load several
        secrets best-effort catch per entry.
        """
