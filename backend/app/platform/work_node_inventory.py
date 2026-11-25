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


@dataclass(frozen=True)
class LaunchOutcome:
    """What a launch attempt's recorded provider requests settled to.

    ``created``: a VM exists for it (``instance``); ``none``: the provider confirms none was made;
    ``unknown``: not yet provable either way, so the node's credentials are kept.
    """

    state: str
    instance: ManagedInstance | None = None


def journal_entry(key: str, state: str, **fields) -> dict:
    """One recorded provider request of a launch attempt: written before it is sent ("requested"),
    then updated with the provider's answer ("created" or "failed")."""
    return {"key": key, "state": state, **fields}
