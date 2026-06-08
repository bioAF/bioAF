"""GCP Secret Manager implementation of SecretsProvider (Phase 9C)."""

from __future__ import annotations

from app.adapters.secrets.base import SecretsProvider


class GcpSecretsProvider(SecretsProvider):
    """Reads secret versions from Google Secret Manager."""

    def __init__(self, project_id: str):
        self.project_id = project_id

    def access_secret(self, name: str) -> str:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        full_name = f"projects/{self.project_id}/secrets/{name}/versions/latest"
        response = client.access_secret_version(request={"name": full_name})
        return response.payload.data.decode("UTF-8")
