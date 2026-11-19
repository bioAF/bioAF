"""Releasing a notebook session's pod and LoadBalancer Service.

One operation deletes both, each independently: a failure on one never skips the other. A
resource is released only when it is confirmed absent. A read error is not absence, a resource
whose labels or selector name another session is never deleted, and every delete carries the UID
that was verified, so a same-named replacement is not removed by mistake.
"""

from app.adapters.models import NotebookWorkload
from app.adapters.notebooks.lifecycle import release_workload
from tests.support.notebook_k8s_fakes import NAMESPACE, FakeCore, notebook_pod, notebook_service

POD = "bioaf-notebook-42"
SVC = "bioaf-notebook-svc-42"
WORKLOAD = NotebookWorkload(
    session_id=42, user_id=7, session_type="jupyter", namespace=NAMESPACE, pod_name=POD, service_name=SVC
)


def test_both_already_gone_is_complete_without_deleting():
    core = FakeCore()
    result = release_workload(core, WORKLOAD)
    assert (result.pod.state, result.service.state) == ("absent", "absent")
    assert result.complete and not result.blocked
    assert core.deletes() == []


def test_verified_resources_are_deleted_with_their_uid_and_complete_only_once_gone():
    core = FakeCore(pods=[notebook_pod(uid="p1")], services=[notebook_service(uid="s1")])

    first = release_workload(core, WORKLOAD)

    assert ("delete", "pod", POD, NAMESPACE, "p1") in core.calls
    assert ("delete", "service", SVC, NAMESPACE, "s1") in core.calls
    assert (first.pod.state, first.service.state) == ("terminating", "terminating")
    assert not first.complete

    core.finish_terminating()
    second = release_workload(core, WORKLOAD)
    assert second.complete
    assert len(core.deletes()) == 2  # nothing deleted again


def test_a_resource_already_terminating_is_not_deleted_again_and_stays_pending():
    core = FakeCore(pods=[notebook_pod(deleting=True)], services=[notebook_service()])
    core.delete_is_immediate = True
    result = release_workload(core, WORKLOAD)
    assert result.pod.state == "terminating"
    assert result.service.state == "absent"
    assert [c[1] for c in core.deletes()] == ["service"]
    assert not result.complete


def test_a_failed_pod_delete_still_attempts_the_service():
    core = FakeCore(pods=[notebook_pod()], services=[notebook_service()], errors={("delete", "pod", POD): 403})
    core.delete_is_immediate = True
    result = release_workload(core, WORKLOAD)
    assert result.pod.state == "failed"
    assert result.service.state == "absent"
    assert not result.complete and not result.blocked


def test_a_failed_service_read_is_not_absence():
    core = FakeCore(pods=[notebook_pod()], services=[notebook_service()], errors={("read", "service", SVC): 500})
    core.delete_is_immediate = True
    result = release_workload(core, WORKLOAD)
    assert result.service.state == "failed"
    assert result.pod.state == "absent"
    assert not result.complete


def test_a_pod_belonging_to_another_session_user_or_type_is_never_deleted():
    for labels in (
        {"bioaf.io/session": "43", "bioaf.io/user": "7", "bioaf.io/type": "jupyter"},
        {"bioaf.io/session": "42", "bioaf.io/user": "8", "bioaf.io/type": "jupyter"},
        {"bioaf.io/session": "42", "bioaf.io/user": "7", "bioaf.io/type": "rstudio"},
        {},
    ):
        core = FakeCore(pods=[notebook_pod(labels=labels)])
        result = release_workload(core, WORKLOAD)
        assert result.pod.state == "blocked", labels
        assert result.blocked and not result.complete
        assert core.deletes() == []


def test_a_service_that_selects_another_session_is_never_deleted():
    for svc in (notebook_service(selector={"bioaf.io/session": "43"}), notebook_service(labels={})):
        core = FakeCore(services=[svc])
        result = release_workload(core, WORKLOAD)
        assert result.service.state == "blocked"
        assert core.deletes() == []


def test_a_replacement_with_the_same_name_is_not_deleted():
    """The pod changed between the verified read and the delete: the UID precondition refuses it."""
    core = FakeCore(pods=[notebook_pod(uid="p1")])
    original = core.read_namespaced_pod

    def read_then_replace(name, namespace, **kw):
        pod = original(name, namespace, **kw)
        core.pods[POD] = notebook_pod(uid="p2")
        return pod

    core.read_namespaced_pod = read_then_replace
    result = release_workload(core, WORKLOAD)
    assert result.pod.state == "failed"
    assert POD in core.pods and core.pods[POD].metadata.deletion_timestamp is None


def test_requests_are_bounded():
    seen = []

    class Recording(FakeCore):
        def read_namespaced_pod(self, name, namespace, **kw):
            seen.append(kw.get("_request_timeout"))
            return super().read_namespaced_pod(name, namespace, **kw)

    release_workload(Recording(), WORKLOAD, request_timeout=7.5)
    assert seen == [7.5]


def test_an_unexpected_client_error_is_a_failure_not_a_crash():
    class Broken(FakeCore):
        def read_namespaced_pod(self, *a, **k):
            raise RuntimeError("connection reset")

    result = release_workload(Broken(), WORKLOAD)
    assert result.pod.state == "failed"
    assert "RuntimeError" in (result.pod.detail or "")
