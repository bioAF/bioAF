"""Kubernetes stand-ins for terminal-instruction tests.

Real ``kubernetes.client`` model objects shaped like what launch creates (the Job from
``k8s_manifest``, the Pod from the notebook adapter) and a fake API that answers only the metadata
reads resolution needs: any other call, including pod exec, fails the test.
"""

import threading

from kubernetes.client import (
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1Job,
    V1ObjectMeta,
    V1OwnerReference,
    V1Pod,
    V1PodList,
    V1PodStatus,
)
from kubernetes.client.exceptions import ApiException

GKE = {
    "gke_cluster_name": "bioaf-lab-abc123",
    "gcp_project_id": "lab-proj-1",
    "gke_cluster_location": "us-west1",
    "gcp_region": "us-west1",
}
CONTEXT = "gke_lab-proj-1_us-west1_bioaf-lab-abc123"
JOB = "bioaf-pipeline-42-1731700000"
JOB_UID = "11111111-2222-3333-4444-555555555555"


def container_status(name, state):
    states = {
        "running": V1ContainerState(running=V1ContainerStateRunning()),
        "waiting": V1ContainerState(waiting=V1ContainerStateWaiting(reason="ContainerCreating")),
        "terminated": V1ContainerState(terminated=V1ContainerStateTerminated(exit_code=0)),
    }
    return V1ContainerStatus(
        name=name, state=states[state], ready=state == "running", restart_count=0, image="i", image_id="i"
    )


def pipeline_job(run_id="42", uid=JOB_UID, deleting=False, name=JOB, namespace="bioaf-pipelines"):
    return V1Job(
        metadata=V1ObjectMeta(
            name=name,
            namespace=namespace,
            uid=uid,
            labels={"bioaf.io/pipeline-run": run_id, "bioaf.io/pool": "pipelines"},
            deletion_timestamp="2026-11-16T00:00:00Z" if deleting else None,
        )
    )


def head_pod(
    name=None, owner_uid=JOB_UID, pipeline="running", sidecars=(), deleting=False, job=JOB, namespace="bioaf-pipelines"
):
    return V1Pod(
        metadata=V1ObjectMeta(
            name=name or f"{job}-x7k2p",
            namespace=namespace,
            labels={"job-name": job},
            owner_references=[V1OwnerReference(api_version="batch/v1", kind="Job", name=job, uid=owner_uid)],
            deletion_timestamp="2026-11-16T00:00:00Z" if deleting else None,
        ),
        status=V1PodStatus(
            phase="Running",
            container_statuses=[container_status("pipeline", pipeline), *[container_status(n, s) for n, s in sidecars]],
        ),
    )


def task_pod():
    # Nextflow task pods carry the run label but are owned by no Job.
    return V1Pod(
        metadata=V1ObjectMeta(name="nf-abc123", namespace="bioaf-pipelines", labels={"bioaf.io/run-id": "42"}),
        status=V1PodStatus(phase="Running", container_statuses=[container_status("nf-abc123", "running")]),
    )


def notebook_pod(
    session="7",
    user="3",
    kind="jupyter",
    notebook="running",
    sidecars=(("gcs-sync", "running"),),
    deleting=False,
    name=None,
    namespace="bioaf-notebooks",
):
    return V1Pod(
        metadata=V1ObjectMeta(
            name=name or f"bioaf-notebook-{session}",
            namespace=namespace,
            labels={
                "bioaf.io/session": session,
                "bioaf.io/user": user,
                "bioaf.io/type": kind,
                "bioaf.io/pool": "interactive",
            },
            deletion_timestamp="2026-11-16T00:00:00Z" if deleting else None,
        ),
        status=V1PodStatus(
            phase="Running",
            container_statuses=[container_status("notebook", notebook), *[container_status(n, s) for n, s in sidecars]],
        ),
    )


class FakeK8s:
    """CoreV1Api + BatchV1Api stand-in that serves only the metadata reads resolution needs."""

    def __init__(self, *, job=None, pods=(), pod=None, error=None, delay=0.0):
        self.job, self.pods, self.pod, self.error, self.delay = job, list(pods), pod, error, delay
        self.calls: list[tuple[str, dict]] = []

    def _call(self, name, kwargs):
        self.calls.append((name, kwargs))
        assert kwargs.get("_request_timeout") is not None and 0 < kwargs["_request_timeout"] <= 10
        if self.delay:
            threading.Event().wait(self.delay)
        if self.error is not None:
            raise self.error

    def read_namespaced_job(self, name, namespace, **kw):
        self._call("read_namespaced_job", {"name": name, "namespace": namespace, **kw})
        if self.job is None or name != self.job.metadata.name:
            raise ApiException(status=404, reason="Not Found")
        return self.job

    def list_namespaced_pod(self, namespace, **kw):
        self._call("list_namespaced_pod", {"namespace": namespace, **kw})
        return V1PodList(items=self.pods)

    def read_namespaced_pod(self, name, namespace, **kw):
        self._call("read_namespaced_pod", {"name": name, "namespace": namespace, **kw})
        if self.pod is None or name != self.pod.metadata.name:
            raise ApiException(status=404, reason="Not Found")
        return self.pod

    def __getattr__(self, name):  # connect_get_namespaced_pod_exec and anything else
        raise AssertionError(f"resolution must not call {name}")
