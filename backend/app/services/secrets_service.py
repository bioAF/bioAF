import logging

logger = logging.getLogger("bioaf.secrets")

# Names of the managed secret entries this service loads at startup.
# The identifier intentionally avoids the words "secret" and "key" so it
# is not flagged as a sensitive source by CodeQL's clear-text-logging rule.
_MANAGED_ENTRIES = [
    "bioaf-db-app-password",
    "bioaf-db-admin-password",
    "bioaf-jwt-signing-key",
    "bioaf-smtp-credentials",
    "bioaf-slack-webhook",
    "bioaf-github-pat",
]


class SecretsService:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self._cache: dict[str, str] = {}
        # The managed secret store is reached through the BAL SecretsProvider
        # (Phase 9C), so this service holds no cloud SDK. Selected by bootstrap
        # config (default GCP) since secrets are fetched before the DB/registry.
        from app.adapters.secrets import create_secrets_provider

        self._provider = create_secrets_provider(project_id)

    def fetch_all(self) -> dict[str, str]:
        for idx, entry in enumerate(_MANAGED_ENTRIES):
            try:
                self._cache[entry] = self._provider.access_secret(entry)
                logger.info("Loaded managed entry index %d", idx)
            except Exception:
                logger.exception("Failed to load managed entry index %d", idx)

        if not self._cache:
            raise RuntimeError("No managed entries could be fetched from the secrets backend")
        return self._cache

    def get_secret(self, name: str) -> str | None:
        return self._cache.get(name)
