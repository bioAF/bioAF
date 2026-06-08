"""IamProvider: the BAL seam for service-account management (Phase 9B).

A platform-service provider (distinct from the five runtime adapter categories in
app/adapters/base.py): the orphan sweep enumerates and deletes service accounts
created by the compute module. GCP wraps ``iam_admin_v1``; AWS would wrap IAM;
on-prem could use local accounts or report the capability absent. Service
accounts are normalized to ``ServiceAccount`` so callers never touch a backend
SDK or its name format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ServiceAccount:
    """A managed service account: its full email and the backend-neutral id.

    ``account_id`` is the local part of the email (before ``@``); callers match
    against it without knowing the backend's email-domain convention.
    """

    email: str
    account_id: str


class IamProvider(ABC):
    """Enumerate and delete service accounts on the active IAM backend."""

    @abstractmethod
    def list_service_accounts(self, project_id: str) -> list[ServiceAccount]:
        """Return all service accounts under ``project_id``."""

    @abstractmethod
    def delete_service_account(self, project_id: str, account_id: str) -> None:
        """Delete the service account identified by ``account_id`` in ``project_id``."""
