import logging

logger = logging.getLogger("bioaf.secrets")

SECRET_NAMES = [
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

    def fetch_all(self) -> dict[str, str]:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        for entry in SECRET_NAMES:
            try:
                name = f"projects/{self.project_id}/secrets/{entry}/versions/latest"
                response = client.access_secret_version(request={"name": name})
                self._cache[entry] = response.payload.data.decode("UTF-8")
                logger.info("Fetched secret entry %s", entry)
            except Exception:
                logger.exception("Could not fetch secret entry %s", entry)

        if not self._cache:
            raise RuntimeError("No secrets could be fetched from Secret Manager")
        return self._cache

    def get_secret(self, name: str) -> str | None:
        return self._cache.get(name)
