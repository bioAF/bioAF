"""Work Node provider inventory records, shared by the adapters and the first-update gate (plan 03A)."""

from __future__ import annotations

from dataclasses import dataclass


class InventoryUnavailable(Exception):
    """The provider could not list Work Node instances; safety cannot be proven."""


@dataclass(frozen=True)
class ManagedInstance:
    """One existing (not terminated/deleted) instance labelled as a bioAF Work Node."""

    name: str
    session_id: str | None
    zone: str | None = None
    provider_instance_id: str | None = None
    launch_attempt_id: str | None = None
