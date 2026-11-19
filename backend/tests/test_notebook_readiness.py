"""How far along a notebook launch is, and when to stop waiting for it.

A cold start can spend many minutes adding a node and downloading a large image; that is progress,
not failure. A pod phase of Unknown or a failed read is uncertainty. Only a confirmed terminal
state fails a launch early. Everything else fails at one overall deadline, counted from the
recorded launch time, and "running" needs the notebook container itself and a usable address.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.models import ServiceState
from app.adapters.notebooks.lifecycle import assess_pod
from app.adapters.notebooks.readiness import STARTUP_DEADLINE, decide_launch, launch_started_at
from tests.support.notebook_k8s_fakes import notebook_pod

T0 = datetime(2026, 11, 13, 2, 44, 59, tzinfo=timezone.utc)


def _events(*pairs):
    return [{"reason": r, "message": m} for r, m in pairs]


@pytest.mark.parametrize(
    "pod",
    [
        notebook_pod(phase="Pending", ready=False, notebook=None),
        notebook_pod(phase="Pending", ready=False, notebook="waiting"),
        notebook_pod(phase="Pending", ready=False, notebook="waiting", notebook_reason="ErrImagePull"),
        notebook_pod(phase="Pending", ready=False, notebook=None, init="running"),
        # The sidecar alone running is not the notebook running.
        notebook_pod(phase="Running", ready=True, notebook="waiting"),
        notebook_pod(phase="Running", ready=False, notebook="running"),
    ],
)
def test_startup_progress(pod):
    assert assess_pod(pod, _events(("TriggeredScaleUp", "Pod triggered scale-up"))).state == "starting"


def test_a_ready_notebook_container():
    assert assess_pod(notebook_pod(), []).state == "ready"


def test_pod_phase_unknown_is_uncertainty():
    assert assess_pod(notebook_pod(phase="Unknown", ready=False, notebook=None), []).state == "uncertain"


@pytest.mark.parametrize(
    "pod,events,reason",
    [
        (notebook_pod(phase="Failed", ready=False, notebook="terminated"), [], "unknown"),
        (
            notebook_pod(phase="Failed", ready=False, notebook="terminated", notebook_reason="OOMKilled"),
            [],
            "oom_killed",
        ),
        (notebook_pod(phase="Succeeded", ready=False, notebook="terminated"), [], "unknown"),
        (
            notebook_pod(phase="Pending", ready=False, notebook="waiting", notebook_reason="InvalidImageName"),
            [],
            "image_pull_failed",
        ),
        (
            notebook_pod(
                phase="Pending", ready=False, notebook="waiting", notebook_reason="CreateContainerConfigError"
            ),
            [],
            "unknown",
        ),
        (
            notebook_pod(phase="Pending", ready=False, notebook="waiting", notebook_reason="ImagePullBackOff"),
            _events(("Failed", 'Failed to pull image "x": manifest unknown')),
            "image_pull_failed",
        ),
        (notebook_pod(phase="Pending", ready=False, notebook=None, init="terminated"), [], "unknown"),
        (notebook_pod(deleting=True), [], "unknown"),
    ],
)
def test_confirmed_terminal_failures(pod, events, reason):
    result = assess_pod(pod, events)
    assert result.state == "failed"
    assert result.reason == reason
    assert result.message and "scale-up" not in result.message.lower()


def test_a_retrying_image_pull_without_a_permanent_cause_is_still_progress():
    pod = notebook_pod(phase="Pending", ready=False, notebook="waiting", notebook_reason="ImagePullBackOff")
    events = _events(("Pulling", 'Pulling image "x"'), ("Failed", "Failed to pull image: i/o timeout"))
    assert assess_pod(pod, events).state == "starting"


# -- the decision ------------------------------------------------------------------------------


def test_the_26_second_observation_waits():
    """Acceptance 11: Unknown with only TriggeredScaleUp shortly after launch does not fail."""
    d = decide_launch(ServiceState.UNKNOWN, None, {}, started=T0, now=T0 + timedelta(seconds=26))
    assert d.action == "wait"


def test_a_cold_start_past_five_minutes_waits_then_becomes_ready_with_its_address():
    assert decide_launch(ServiceState.STARTING, None, {}, started=T0, now=T0 + timedelta(minutes=6)).action == "wait"
    ready = decide_launch(ServiceState.RUNNING, "http://1.2.3.4:8888", {}, started=T0, now=T0 + timedelta(minutes=13))
    assert (ready.action, ready.access_url) == ("ready", "http://1.2.3.4:8888")


def test_running_without_an_address_is_not_ready():
    d = decide_launch(ServiceState.RUNNING, None, {}, started=T0, now=T0 + timedelta(minutes=10))
    assert d.action == "wait"


def test_a_confirmed_failure_fails_now_with_its_cause():
    d = decide_launch(
        ServiceState.ERROR, None, {"failure_reason": "oom_killed", "failure_message": "OOMKilled"}, started=T0, now=T0
    )
    assert (d.action, d.reason, d.message) == ("fail", "oom_killed", "OOMKilled")


@pytest.mark.parametrize("observed", [ServiceState.STARTING, ServiceState.UNKNOWN, ServiceState.RUNNING])
def test_the_overall_deadline_fails_an_unfinished_launch_explicitly(observed):
    d = decide_launch(observed, None, {}, started=T0, now=T0 + STARTUP_DEADLINE)
    assert (d.action, d.reason) == ("fail", "startup_timeout")
    assert "30 minutes" in (d.message or "")


def test_a_timeout_keeps_an_observed_cause():
    details = {"waiting_reason": "resource_exhausted", "waiting_message": "GCE out of resources"}
    d = decide_launch(ServiceState.STARTING, None, details, started=T0, now=T0 + STARTUP_DEADLINE)
    assert d.reason == "resource_exhausted" and "GCE out of resources" in (d.message or "")


def test_the_deadline_counts_from_the_recorded_launch_not_the_latest_observation():
    metadata = {"lifecycle": {"reserved_at": T0.isoformat()}}
    later = T0 + timedelta(minutes=20)
    assert launch_started_at(metadata, started_at=later, created_at=later) == T0
    assert launch_started_at({}, started_at=later, created_at=T0) == later
    assert launch_started_at(None, started_at=None, created_at=T0) == T0


def test_the_deadline_is_thirty_minutes():
    assert STARTUP_DEADLINE == timedelta(minutes=30)
