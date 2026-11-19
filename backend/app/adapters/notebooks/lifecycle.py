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


def _pod_belongs(pod, w: NotebookWorkload) -> bool:
    labels = (pod.metadata.labels or {}) if pod.metadata else {}
    return (
        labels.get("bioaf.io/session") == str(w.session_id)
        and labels.get("bioaf.io/user") == str(w.user_id)
        and labels.get("bioaf.io/type") == w.session_type
    )


def _service_belongs(svc, w: NotebookWorkload) -> bool:
    labels = (svc.metadata.labels or {}) if svc.metadata else {}
    selector = (svc.spec.selector or {}) if svc.spec else {}
    session = str(w.session_id)
    return labels.get("bioaf.io/session") == session and selector == {"bioaf.io/session": session}


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
