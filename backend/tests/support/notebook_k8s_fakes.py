"""Kubernetes stand-ins for notebook lifecycle tests.

Real ``kubernetes.client`` model objects shaped like what notebook launch creates, served by a fake
CoreV1Api that records every call. Reads and deletes behave like the API server: a missing object
is a 404, a delete whose UID precondition no longer matches is a 409, and an accepted pod delete
leaves the pod terminating until the test removes it.
"""

from __future__ import annotations

from kubernetes.client import (
    V1Container,
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1ObjectMeta,
    V1Pod,
    V1PodCondition,
    V1PodSpec,
    V1PodStatus,
    V1Service,
    V1ServiceSpec,
)
from kubernetes.client.exceptions import ApiException

NAMESPACE = "bioaf-notebooks"


def _api_error(status: int) -> ApiException:
    return ApiException(status=status, reason={404: "Not Found", 403: "Forbidden", 409: "Conflict"}.get(status, "Err"))


def container_status(name: str, state: str, *, reason: str | None = None, restarts: int = 0, last_terminated=False):
    states = {
        "running": V1ContainerState(running=V1ContainerStateRunning()),
        "waiting": V1ContainerState(waiting=V1ContainerStateWaiting(reason=reason or "ContainerCreating")),
        "terminated": V1ContainerState(terminated=V1ContainerStateTerminated(exit_code=1, reason=reason or "Error")),
    }
    last = V1ContainerState(terminated=V1ContainerStateTerminated(exit_code=1)) if last_terminated else None
    return V1ContainerStatus(
        name=name,
        state=states[state],
        last_state=last,
        ready=state == "running",
        restart_count=restarts,
        image="i",
        image_id="i",
    )


def notebook_pod(
    session_id: int = 42,
    user_id: int = 7,
    session_type: str = "jupyter",
    *,
    uid: str = "pod-uid-1",
    phase: str = "Running",
    ready: bool = True,
    notebook: str | None = "running",
    notebook_reason: str | None = None,
    init: str | None = None,
    init_reason: str | None = None,
    deleting: bool = False,
    labels: dict | None = None,
) -> V1Pod:
    statuses = [container_status("notebook", notebook, reason=notebook_reason)] if notebook else None
    init_statuses = [container_status("gcs-sync-init", init, reason=init_reason)] if init else None
    return V1Pod(
        metadata=V1ObjectMeta(
            name=f"bioaf-notebook-{session_id}",
            namespace=NAMESPACE,
            uid=uid,
            labels=labels
            if labels is not None
            else {
                "bioaf.io/session": str(session_id),
                "bioaf.io/user": str(user_id),
                "bioaf.io/type": session_type,
                "bioaf.io/pool": "interactive",
            },
            deletion_timestamp="2026-11-18T00:00:00Z" if deleting else None,
        ),
        spec=V1PodSpec(containers=[V1Container(name="notebook")]),
        status=V1PodStatus(
            phase=phase,
            conditions=[V1PodCondition(type="Ready", status="True" if ready else "False")],
            container_statuses=statuses,
            init_container_statuses=init_statuses,
        ),
    )


def notebook_service(session_id: int = 42, *, uid: str = "svc-uid-1", selector: dict | None = None, labels=None):
    return V1Service(
        metadata=V1ObjectMeta(
            name=f"bioaf-notebook-svc-{session_id}",
            namespace=NAMESPACE,
            uid=uid,
            labels=labels if labels is not None else {"bioaf.io/session": str(session_id)},
        ),
        spec=V1ServiceSpec(selector=selector if selector is not None else {"bioaf.io/session": str(session_id)}),
    )


class FakeCore:
    """CoreV1Api stand-in for pods and Services in one namespace."""

    def __init__(self, *, pods=(), services=(), errors: dict | None = None, events: list[tuple] = ()):
        self.events = list(events)  # (reason, message) for the session's pod
        self.pods = {p.metadata.name: p for p in pods}
        self.services = {s.metadata.name: s for s in services}
        # (verb, kind, name) -> HTTP status to raise, e.g. ("delete", "pod", "bioaf-notebook-42"): 403
        self.errors = dict(errors or {})
        self.calls: list[tuple] = []
        # Accepted deletes leave the object terminating unless this is True.
        self.delete_is_immediate = False

    def _maybe_fail(self, verb, kind, name):
        status = self.errors.get((verb, kind, name))
        if status:
            raise _api_error(status)

    def _read(self, store, kind, name, namespace):
        self.calls.append(("read", kind, name, namespace))
        self._maybe_fail("read", kind, name)
        obj = store.get(name)
        if obj is None or namespace != NAMESPACE:
            raise _api_error(404)
        return obj

    def _delete(self, store, kind, name, namespace, body):
        uid = getattr(getattr(body, "preconditions", None), "uid", None)
        self.calls.append(("delete", kind, name, namespace, uid))
        self._maybe_fail("delete", kind, name)
        obj = store.get(name)
        if obj is None or namespace != NAMESPACE:
            raise _api_error(404)
        if uid is not None and obj.metadata.uid != uid:
            raise _api_error(409)
        if self.delete_is_immediate:
            del store[name]
        else:
            obj.metadata.deletion_timestamp = "2026-11-18T00:00:00Z"

    def read_namespaced_pod(self, name, namespace, **_):
        return self._read(self.pods, "pod", name, namespace)

    def read_namespaced_service(self, name, namespace, **_):
        return self._read(self.services, "service", name, namespace)

    def delete_namespaced_pod(self, name, namespace, body=None, **_):
        return self._delete(self.pods, "pod", name, namespace, body)

    def delete_namespaced_service(self, name, namespace, body=None, **_):
        return self._delete(self.services, "service", name, namespace, body)

    def list_namespaced_pod(self, namespace, label_selector=None, **_):
        from types import SimpleNamespace

        self.calls.append(("list", "pod", label_selector))
        return SimpleNamespace(items=list(self.pods.values()))

    def list_namespaced_service(self, namespace, label_selector=None, **_):
        from types import SimpleNamespace

        self.calls.append(("list", "service", label_selector))
        return SimpleNamespace(items=list(self.services.values()))

    def list_namespaced_event(self, namespace, field_selector=None, **_):
        from types import SimpleNamespace

        self.calls.append(("events", field_selector))
        return SimpleNamespace(items=[SimpleNamespace(reason=r, message=m) for r, m in self.events])

    def finish_terminating(self):
        """The API server finishes every pending deletion."""
        for store in (self.pods, self.services):
            for name in [n for n, o in store.items() if o.metadata.deletion_timestamp]:
                del store[name]

    def deletes(self):
        return [c for c in self.calls if c[0] == "delete"]
