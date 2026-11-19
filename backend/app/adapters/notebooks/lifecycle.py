"""Kubernetes lifecycle operations for one interactive notebook session's resources.

Synchronous: callers run these off the event loop. Every request carries a timeout. Nothing here
writes to the database; callers record the typed outcomes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from kubernetes.client import V1DeleteOptions, V1Preconditions
from kubernetes.client.exceptions import ApiException

from app.adapters.models import NotebookWorkload, ResourceRelease, WorkloadRelease

logger = logging.getLogger("bioaf.adapters.notebooks.lifecycle")

# Each provider request; one release makes at most three per resource (read, delete, confirm).
REQUEST_TIMEOUT_SECONDS = 15.0


def _status(exc: BaseException) -> int | None:
    return exc.status if isinstance(exc, ApiException) else None


def _failure(exc: BaseException) -> str:
    status = _status(exc)
    return f"HTTP {status}" if status is not None else type(exc).__name__


def _same_organization(labels: dict, w: NotebookWorkload) -> bool:
    """A present organization label must name the session's organization; older resources have none."""
    label = labels.get("bioaf.io/organization")
    return label is None or (w.organization_id is not None and label == str(w.organization_id))


def _pod_belongs(pod, w: NotebookWorkload) -> bool:
    labels = (pod.metadata.labels or {}) if pod.metadata else {}
    return (
        _same_organization(labels, w)
        and labels.get("bioaf.io/session") == str(w.session_id)
        and labels.get("bioaf.io/user") == str(w.user_id)
        and labels.get("bioaf.io/type") == w.session_type
    )


def _service_belongs(svc, w: NotebookWorkload) -> bool:
    labels = (svc.metadata.labels or {}) if svc.metadata else {}
    selector = (svc.spec.selector or {}) if svc.spec else {}
    session = str(w.session_id)
    return (
        _same_organization(labels, w)
        and labels.get("bioaf.io/session") == session
        and selector == {"bioaf.io/session": session}
    )


def _release_one(
    kind: str,
    name: str,
    read: Callable,
    delete: Callable,
    belongs: Callable[[object], bool],
    w: NotebookWorkload,
    timeout: float,
) -> ResourceRelease:
    try:
        obj = read(name=name, namespace=w.namespace, _request_timeout=timeout)
    except Exception as exc:
        if _status(exc) == 404:
            return ResourceRelease(state="absent")
        logger.warning("Could not read notebook %s %s for session %s: %s", kind, name, w.session_id, _failure(exc))
        return ResourceRelease(state="failed", detail=f"read: {_failure(exc)}")

    if not belongs(obj):
        logger.warning("Notebook %s %s does not carry session %s's identity; not deleting it", kind, name, w.session_id)
        return ResourceRelease(state="blocked", detail="identity mismatch")
    if obj.metadata.deletion_timestamp:
        return ResourceRelease(state="terminating")

    uid = obj.metadata.uid
    body = V1DeleteOptions(preconditions=V1Preconditions(uid=uid)) if uid else None
    try:
        delete(name=name, namespace=w.namespace, body=body, _request_timeout=timeout)
    except Exception as exc:
        if _status(exc) == 404:
            return ResourceRelease(state="absent")
        # 409: the UID precondition failed, i.e. it was replaced after the read; re-verify next time.
        logger.warning("Could not delete notebook %s %s for session %s: %s", kind, name, w.session_id, _failure(exc))
        return ResourceRelease(state="failed", detail=f"delete: {_failure(exc)}")
    logger.info("Deletion requested for notebook %s %s (session %s)", kind, name, w.session_id)
    # Absent only when the API server says so; anything else is checked again next time.
    try:
        read(name=name, namespace=w.namespace, _request_timeout=timeout)
    except Exception as exc:
        if _status(exc) == 404:
            return ResourceRelease(state="absent")
    return ResourceRelease(state="terminating")


def pod_ownership(core, w: NotebookWorkload, *, request_timeout: float = REQUEST_TIMEOUT_SECONDS) -> str:
    """``owned``, ``absent``, ``foreign`` (another workload holds the name), or ``unknown`` (unreadable)."""
    try:
        pod = core.read_namespaced_pod(name=w.pod_name, namespace=w.namespace, _request_timeout=request_timeout)
    except Exception as exc:
        if _status(exc) == 404:
            return "absent"
        logger.warning("Could not read notebook pod %s for session %s: %s", w.pod_name, w.session_id, _failure(exc))
        return "unknown"
    return "owned" if _pod_belongs(pod, w) else "foreign"


def service_ownership(core, w: NotebookWorkload, *, request_timeout: float = REQUEST_TIMEOUT_SECONDS) -> str:
    """``owned``, ``absent``, ``foreign``, or ``unknown``: the same rule cleanup applies to the Service."""
    try:
        svc = core.read_namespaced_service(name=w.service_name, namespace=w.namespace, _request_timeout=request_timeout)
    except Exception as exc:
        if _status(exc) == 404:
            return "absent"
        logger.warning(
            "Could not read notebook Service %s for session %s: %s", w.service_name, w.session_id, _failure(exc)
        )
        return "unknown"
    return "owned" if _service_belongs(svc, w) else "foreign"


def pod_is_owned(pod, w: NotebookWorkload) -> bool:
    """The ownership rule for a pod already read (readiness reads it once for both purposes)."""
    return _pod_belongs(pod, w)


def release_workload(core, w: NotebookWorkload, *, request_timeout: float = REQUEST_TIMEOUT_SECONDS) -> WorkloadRelease:
    """Delete the session's pod and Service, each independently; complete only when both are gone."""
    pod = _release_one(
        "pod",
        w.pod_name,
        core.read_namespaced_pod,
        core.delete_namespaced_pod,
        lambda p: _pod_belongs(p, w),
        w,
        request_timeout,
    )
    service = _release_one(
        "service",
        w.service_name,
        core.read_namespaced_service,
        core.delete_namespaced_service,
        lambda s: _service_belongs(s, w),
        w,
        request_timeout,
    )
    return WorkloadRelease(pod=pod, service=service)


# -- launch assessment ---------------------------------------------------------------------------

# Waiting reasons that never resolve by waiting: the pod spec or image reference is wrong.
_PERMANENT_WAITING = {
    "InvalidImageName": "image_pull_failed",
    "ErrImageNeverPull": "image_pull_failed",
    "CreateContainerConfigError": "unknown",
    "CreateContainerError": "unknown",
}
# Registry answers that a retry cannot change (the transient ones, like timeouts, keep retrying).
_PERMANENT_PULL_SIGNALS = ("manifest unknown", "not found", "unauthorized", "denied", "forbidden", "403", "404")
_PULL_RETRY_REASONS = ("ErrImagePull", "ImagePullBackOff")
_MAX_MESSAGE = 500


class PodAssessment:
    """``state``: starting, ready, uncertain, or failed (with a safe ``reason`` and ``message``)."""

    __slots__ = ("state", "reason", "message", "waiting_reason")

    def __init__(self, state: str, reason: str | None = None, message: str | None = None, waiting_reason=None):
        self.state = state
        self.reason = reason
        self.message = message[:_MAX_MESSAGE] if message else message
        self.waiting_reason = waiting_reason


def _set(value) -> bool:
    """A real timestamp or value (not absent, and not a test double's auto-attribute)."""
    from datetime import datetime

    return isinstance(value, (str, datetime))


def _named(statuses, name: str):
    return next((s for s in statuses or [] if getattr(s, "name", None) == name), None)


def assess_pod(pod, events: list[dict], *, container: str = "notebook") -> PodAssessment:
    """Where an interactive session's pod is in its startup, from the pod and its current events."""
    from app.adapters.failure_classification import classify_pod_failure

    meta = pod.metadata
    status = pod.status
    if _set(getattr(meta, "deletion_timestamp", None)):
        return PodAssessment("failed", "unknown", "The session's workload is being deleted.")
    phase = getattr(status, "phase", None)
    if phase == "Unknown":
        return PodAssessment("uncertain")

    for init in getattr(status, "init_container_statuses", None) or []:
        term = getattr(init.state, "terminated", None) if init.state else None
        if term is not None and term.exit_code not in (0, None):
            return PodAssessment(
                "failed", "unknown", f"Session setup step {init.name} failed ({term.reason or 'error'})."
            )

    main = _named(getattr(status, "container_statuses", None), container)
    state = getattr(main, "state", None) if main else None
    term = getattr(state, "terminated", None) if state else None
    if phase in ("Failed", "Succeeded") or term is not None:
        why = getattr(term, "reason", None) if term is not None else None
        if why == "OOMKilled":
            return PodAssessment("failed", "oom_killed", "The notebook ran out of memory (OOMKilled).")
        reason, message = classify_pod_failure(
            [e for e in events if (e.get("reason") or "") not in ("TriggeredScaleUp", "Scheduled", "Pulling", "Pulled")]
        )
        detail = why or phase or "exited"
        return PodAssessment("failed", reason, f"The notebook container stopped before it was ready ({detail}).")

    waiting = getattr(state, "waiting", None) if state else None
    waiting_reason = getattr(waiting, "reason", None) if waiting else None
    if waiting_reason in _PERMANENT_WAITING:
        return PodAssessment(
            "failed",
            _PERMANENT_WAITING[waiting_reason],
            f"The notebook container cannot start ({waiting_reason}).",
            waiting_reason,
        )
    if waiting_reason in _PULL_RETRY_REASONS:
        for e in events:
            message = (e.get("message") or "").lower()
            if "pull" in message and any(sig in message for sig in _PERMANENT_PULL_SIGNALS):
                return PodAssessment(
                    "failed", "image_pull_failed", "The notebook image cannot be downloaded.", waiting_reason
                )

    running = bool(getattr(state, "running", None)) if state else False
    ready = any(
        getattr(c, "type", None) == "Ready" and getattr(c, "status", None) == "True"
        for c in getattr(status, "conditions", None) or []
    )
    if phase == "Running" and running and ready:
        return PodAssessment("ready")
    return PodAssessment("starting", waiting_reason=waiting_reason)
