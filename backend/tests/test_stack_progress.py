"""Stack progress decisions, with no database or Terraform process."""

from app.services.stack_progress import StackProgress, reduce_progress
from app.services.terraform_stream import TerraformProgressEvent


def _event(event_type, completed=0, total=0, **kwargs):
    return TerraformProgressEvent(
        event_type=event_type,
        resources_completed=completed,
        resources_total=total,
        **kwargs,
    )


def _replay(*steps):
    """Run (phase, event) steps through the reducer; return shown (type, done, total)."""
    progress = StackProgress()
    shown = []
    for phase, event in steps:
        progress, out = reduce_progress(progress, phase, event)
        shown.append((out.event_type, out.resources_completed, out.resources_total))
    return progress, shown


def test_starts_empty():
    assert (StackProgress().completed, StackProgress().planned) == (0, 0)


def test_storage_already_deployed_counts_compute_only():
    progress, shown = _replay(
        ("compute", _event("resource_complete", 1, 3)),
        ("compute", _event("apply_complete", 3, 3)),
    )

    assert shown == [("resource_complete", 1, 3), ("apply_complete", 3, 3)]
    assert (progress.completed, progress.planned) == (3, 3)


def test_storage_plus_compute_accumulates():
    progress, shown = _replay(
        ("storage", _event("resource_complete", 2, 2)),
        ("storage", _event("apply_complete", 2, 2)),
        ("compute", _event("planned_resources", 0, 3)),
        ("compute", _event("resource_complete", 1, 3)),
        ("compute", _event("apply_complete", 3, 3)),
    )

    assert shown[2:] == [
        ("planned_resources", 2, 5),
        ("resource_complete", 3, 5),
        ("apply_complete", 5, 5),
    ]
    assert (progress.completed, progress.planned) == (5, 5)


def test_aws_image_build_counts_between_storage_and_compute():
    progress, shown = _replay(
        ("storage", _event("apply_complete", 2, 2)),
        ("image_build", _event("resource_complete", 1, 4)),
        ("image_build", _event("apply_complete", 4, 4)),
        ("compute", _event("resource_complete", 1, 3)),
        ("compute", _event("apply_complete", 3, 3)),
    )

    assert shown[1] == ("resource_complete", 3, 6)
    assert shown[3] == ("resource_complete", 7, 9)
    assert (progress.completed, progress.planned) == (9, 9)


def test_zero_resource_phase_adds_nothing():
    progress, shown = _replay(
        ("storage", _event("apply_complete", 0, 0)),
        ("compute", _event("apply_complete", 2, 2)),
    )

    assert shown == [("apply_complete", 0, 0), ("apply_complete", 2, 2)]
    assert (progress.completed, progress.planned) == (2, 2)


def test_failure_keeps_the_counts_reached():
    progress, shown = _replay(
        ("storage", _event("apply_complete", 2, 2)),
        ("compute", _event("resource_complete", 1, 3)),
        ("compute", _event("apply_error", message="boom")),
    )

    assert shown[-1] == ("apply_error", 3, 5)
    assert (progress.completed, progress.planned) == (3, 5)


def test_events_without_counts_show_the_running_totals():
    _, shown = _replay(
        ("storage", _event("resource_complete", 1, 2)),
        ("storage", _event("progress", message="Refreshing state")),
        ("storage", _event("heartbeat", 1, 2)),
    )

    assert shown == [("resource_complete", 1, 2), ("progress", 1, 2), ("heartbeat", 1, 2)]


def test_terminal_count_replaces_the_running_count():
    progress, _ = _replay(
        ("compute", _event("resource_complete", 5, 5)),
        ("compute", _event("apply_complete", 4, 4)),
    )

    assert (progress.completed, progress.planned) == (4, 4)


def test_reducer_does_not_mutate_its_input_and_keeps_event_fields():
    start = StackProgress()
    event = _event("resource_complete", 1, 2, resource_address="a.b", message="m", log_line="l", extra={"k": 1})

    after, shown = reduce_progress(start, "storage", event)

    assert (start.completed, start.planned) == (0, 0)
    assert (after.completed, after.planned) == (1, 2)
    assert (shown.resource_address, shown.message, shown.log_line, shown.extra) == ("a.b", "m", "l", {"k": 1})


def test_event_ordering_is_preserved_one_to_one():
    steps = [
        ("storage", _event("planned_resources", 0, 1)),
        ("storage", _event("progress")),
        ("storage", _event("resource_complete", 1, 1)),
        ("storage", _event("apply_complete", 1, 1)),
    ]

    _, shown = _replay(*steps)

    assert [s[0] for s in shown] == [step[1].event_type for step in steps]
