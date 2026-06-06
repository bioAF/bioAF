"""Provider capabilities: the typed contract for what a backend can do.

Part of the BioAF Adapter Layer (BAL). Backends differ: a SLURM cluster has no
live cost rate, an NFS mount cannot mint signed URLs, an on-prem install has no
managed billing. Rather than sniffing backend names or guessing from null
fields, each adapter declares a ``ProviderCapabilities`` describing what it
truly supports. The registry merges the active adapters into one capability
surface that services and the UI (Phase 4b) read.

Capability (can this backend ever do X) is distinct from availability (is the
component provisioned and enabled right now, e.g. the Meilisearch ComponentState
gate). This module is about capability only.
"""

from __future__ import annotations

from pydantic import BaseModel


class ProviderCapabilities(BaseModel):
    """What the active backend(s) can do. Every flag defaults to False.

    A False flag means "this backend cannot do this"; the feature is hidden or
    degraded in the UI and a direct API caller gets CapabilityNotSupported. A
    flag is only True when a real implementation backs it (asserted by tests).
    """

    # Compute (Kubernetes today; SLURM later)
    cost_estimation: bool = False
    autoscaling: bool = False
    ssh_exec: bool = False
    spot_retry: bool = False
    job_report: bool = False
    # Storage (GCS today; NFS later)
    signed_url_upload: bool = False
    storage_tier_metrics: bool = False
    # Notebooks / visualization
    notebooks: bool = False
    cellxgene: bool = False
    # Work nodes
    work_nodes: bool = False
    # Platform services reserved for Phase 9 providers (no adapter owns these
    # yet; they default False until MessagingProvider / BillingProvider land).
    messaging: bool = False
    billing: bool = False

    def merge(self, other: "ProviderCapabilities") -> "ProviderCapabilities":
        """Return a new capabilities set that is the logical OR of self and other.

        Used by the registry to aggregate the per-adapter declarations of the
        active stack. Neither operand is mutated.
        """
        return ProviderCapabilities(
            **{
                field: bool(getattr(self, field) or getattr(other, field))
                for field in ProviderCapabilities.model_fields
            }
        )


class CapabilityNotSupported(Exception):
    """Raised when an action is requested that the active backend cannot do.

    Carries the capability flag name so a FastAPI handler (Phase 4b) can map it
    to a 4xx envelope rather than letting the caller hit a 500.
    """

    def __init__(self, capability: str, message: str | None = None) -> None:
        self.capability = capability
        super().__init__(message or f"Capability not supported by the active backend: {capability}")
