"""ImageBuild provider seam (Stage 4e).

The backend-aware seam for the container-image build lifecycle: submit a build
from an uploaded build context, poll its status, and cancel it. The build status
is reported through a neutral enum so the service layer never branches on a
cloud-specific status string.

GCP (Cloud Build) submits a REST build with a ``storageSource`` context and a
docker build step, and reports Cloud Build's native QUEUED/WORKING/SUCCESS/...
status (which already is the neutral contract). AWS (CodeBuild, Stage 6e) calls
StartBuild / BatchGetBuilds and maps CodeBuild's IN_PROGRESS/SUCCEEDED/FAILED/...
onto the same enum, behind this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# Neutral build-status contract. Cloud Build returns these verbatim; other
# backends map their native statuses onto this set. UNKNOWN is the off-contract
# fallback a provider returns when it cannot read the status.
QUEUED = "QUEUED"
WORKING = "WORKING"
SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
CANCELLED = "CANCELLED"
TIMEOUT = "TIMEOUT"
UNKNOWN = "UNKNOWN"

BUILD_STATUSES = frozenset({QUEUED, WORKING, SUCCESS, FAILURE, CANCELLED, TIMEOUT})


class ImageBuildProvider(ABC):
    """Cloud-specific container-image build lifecycle (submit / status / cancel)."""

    @abstractmethod
    def submit_build(
        self,
        credentials: Any,
        config: dict,
        *,
        context_object_uri: str,
        image_uri: str,
        build_sa: str | None,
        timeout: str,
    ) -> str:
        """Submit a build of ``image_uri`` from the build context at
        ``context_object_uri`` (a storage URI). ``build_sa`` is the identity the
        build runs as (or ``None``); ``timeout`` is the cloud's timeout string.
        Returns the backend build id.
        """

    @abstractmethod
    def check_build_status(self, credentials: Any, config: dict, build_id: str) -> str:
        """Return the neutral status for ``build_id`` (``UNKNOWN`` if unreadable)."""

    @abstractmethod
    def cancel_build(self, credentials: Any, config: dict, build_id: str) -> None:
        """Best-effort cancel of ``build_id``; swallow "already finished" errors."""
