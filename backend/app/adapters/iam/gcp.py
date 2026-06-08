"""GCP IAM implementation of IamProvider (Phase 9B)."""

from __future__ import annotations

from app.adapters.iam.base import IamProvider, ServiceAccount


class GcpIamProvider(IamProvider):
    """Enumerates and deletes service accounts via ``iam_admin_v1``."""

    def __init__(self, credentials=None):
        self.credentials = credentials

    def _client(self):
        from google.cloud import iam_admin_v1

        if self.credentials:
            return iam_admin_v1.IAMClient(credentials=self.credentials)
        return iam_admin_v1.IAMClient()

    def list_service_accounts(self, project_id: str) -> list[ServiceAccount]:
        client = self._client()
        accounts = client.list_service_accounts(name=f"projects/{project_id}")
        return [ServiceAccount(email=sa.email, account_id=sa.email.split("@")[0]) for sa in accounts]

    def delete_service_account(self, project_id: str, account_id: str) -> None:
        client = self._client()
        sa_name = f"projects/{project_id}/serviceAccounts/{account_id}@{project_id}.iam.gserviceaccount.com"
        client.delete_service_account(name=sa_name)
