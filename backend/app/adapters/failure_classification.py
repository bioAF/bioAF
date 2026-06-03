"""Map raw GCE/GKE failure text into the small enum the UI shows.

The Notebook + Work Node detail modals show two fields when a session fails:

- failure_reason: a short enum the frontend turns into a label ("Resource
  Failure", "Image Pull Failed", ...). Must come from a fixed set so the UI
  doesn't have to guess.
- failure_message: the underlying error text, shown verbatim so the operator
  can correlate with cloud logs.

`classify_pod_failure` walks a list of K8s pod events (most recent or otherwise)
and returns the strongest signal it finds. Resource-exhausted wins over generic
scheduling failures because telling the user "GCP is out of capacity right now"
is much more actionable than "the scheduler couldn't place this pod."

`classify_gce_vm_failure` does the same for an exception string raised by the
GCE work-node adapter.
"""

from __future__ import annotations

FAILURE_REASON_RESOURCE_EXHAUSTED = "resource_exhausted"
FAILURE_REASON_IMAGE_PULL_FAILED = "image_pull_failed"
FAILURE_REASON_OOM_KILLED = "oom_killed"
FAILURE_REASON_QUOTA_EXCEEDED = "quota_exceeded"
FAILURE_REASON_UNKNOWN = "unknown"


_RESOURCE_EXHAUSTED_SIGNALS = (
    "failedscaleup",
    "gce out of resources",
    "does not have enough resources",
    "zone_resource_pool_exhausted",
    "gcp resources unavailable",  # service-layer wrapper around the GCE error
)

_IMAGE_PULL_SIGNALS = (
    "imagepullbackoff",
    "errimagepull",
    "failed to pull image",
    "manifest unknown",
)

_OOM_SIGNALS = (
    "oomkilled",
    "out of memory",
)

_QUOTA_SIGNALS = (
    "quota_exceeded",
    "quota exceeded",
)


def _match_any(haystack: str, needles: tuple[str, ...]) -> bool:
    haystack_l = haystack.lower()
    return any(n in haystack_l for n in needles)


def classify_pod_failure(events: list[dict]) -> tuple[str, str]:
    """Classify a list of K8s pod events into (failure_reason, failure_message).

    `events` is a list of `{"reason": str, "message": str}` dicts. The order is
    not important; we look for the strongest signal across all of them.

    Returns ("unknown", "<best message>") if no specific failure mode is detected.
    """
    if not events:
        return (FAILURE_REASON_UNKNOWN, "Pod did not become ready within the readiness window.")

    # First pass: resource_exhausted is the most operator-actionable signal.
    for evt in events:
        combined = f"{evt.get('reason') or ''} {evt.get('message') or ''}"
        if _match_any(combined, _RESOURCE_EXHAUSTED_SIGNALS):
            return (
                FAILURE_REASON_RESOURCE_EXHAUSTED,
                evt.get("message") or "GCE is out of capacity for the requested machine type.",
            )

    # Second pass: other known modes.
    for evt in events:
        combined = f"{evt.get('reason') or ''} {evt.get('message') or ''}"
        if _match_any(combined, _IMAGE_PULL_SIGNALS):
            return (
                FAILURE_REASON_IMAGE_PULL_FAILED,
                evt.get("message") or "Failed to pull the container image.",
            )
        if _match_any(combined, _OOM_SIGNALS):
            return (
                FAILURE_REASON_OOM_KILLED,
                evt.get("message") or "Container was killed due to out-of-memory.",
            )

    # Fall through: pick the most-recent event message so the modal isn't empty.
    last_message = events[-1].get("message") or "Pod did not become ready within the readiness window."
    return (FAILURE_REASON_UNKNOWN, last_message)


def classify_gce_vm_failure(error_text: str | None) -> tuple[str, str]:
    """Classify a GCE VM launch failure string into (failure_reason, message)."""
    if not error_text:
        return (FAILURE_REASON_UNKNOWN, "VM did not start within the readiness window.")

    if _match_any(error_text, _RESOURCE_EXHAUSTED_SIGNALS):
        return (FAILURE_REASON_RESOURCE_EXHAUSTED, error_text)
    if _match_any(error_text, _QUOTA_SIGNALS):
        return (FAILURE_REASON_QUOTA_EXCEEDED, error_text)
    return (FAILURE_REASON_UNKNOWN, error_text)
