"""Route-side handling of streamed Terraform operations.

``interrupted`` is an internal outcome. Translating it here, and only here, is
what lets the services keep it out of their failure branches while connected
clients still receive a terminal non-success event they already understand.
"""

from __future__ import annotations

from app.services.terraform_stream import INTERRUPTED_EVENT, TerraformProgressEvent

INTERRUPTED_MESSAGE = "The operation was interrupted before it finished. Check its status, then try again."


def client_event(event: TerraformProgressEvent) -> TerraformProgressEvent:
    """The event a client should see: an interruption reads as ``apply_error``."""
    if event.event_type != INTERRUPTED_EVENT:
        return event
    return TerraformProgressEvent(
        event_type="apply_error",
        message=INTERRUPTED_MESSAGE,
        resources_completed=event.resources_completed,
        resources_total=event.resources_total,
    )
