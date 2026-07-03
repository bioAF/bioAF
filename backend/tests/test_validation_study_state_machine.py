"""State machine for the ValidationStudy aggregate (lit_validation A1, spec-02).

Pure unit tests of the transition map, terminal states, and early-exit paths. No DB, so these
pin the spine's control flow independently of persistence.
"""

from app.models.validation_study import (
    VALIDATION_STUDY_STATES,
    VALIDATION_STUDY_TERMINAL_STATES,
    VALIDATION_STUDY_TRANSITIONS,
    VALIDATION_STUDY_CLASSIFICATIONS,
    can_transition,
    next_states,
    is_terminal,
)


def test_transition_map_covers_every_state_and_targets_are_known():
    assert set(VALIDATION_STUDY_TRANSITIONS) == set(VALIDATION_STUDY_STATES)
    for frm, targets in VALIDATION_STUDY_TRANSITIONS.items():
        for to in targets:
            assert to in VALIDATION_STUDY_STATES, f"{frm} -> unknown state {to}"


def test_happy_path_transitions_are_allowed():
    happy = [
        ("requested", "acquiring_text"),
        ("acquiring_text", "reading"),
        ("reading", "plan_ready"),
        ("plan_ready", "acquiring_data"),
        ("acquiring_data", "setup"),
        ("setup", "running"),
        ("running", "extracting"),
        ("extracting", "comparing"),
        ("comparing", "classified"),
    ]
    for frm, to in happy:
        assert can_transition(frm, to), f"expected {frm} -> {to} to be allowed"


def test_invalid_transitions_are_rejected():
    assert not can_transition("requested", "running")
    assert not can_transition("reading", "setup")
    assert not can_transition("comparing", "reading")
    assert not can_transition("setup", "classified")


def test_terminal_states_have_no_outbound_transitions():
    for terminal in ("classified", "plan_declined", "error"):
        assert terminal in VALIDATION_STUDY_TERMINAL_STATES
        assert next_states(terminal) == []
        assert is_terminal(terminal)
    assert not is_terminal("reading")


def test_early_exit_to_classified_before_running():
    # A study can reach a terminal classification before any pipeline runs (spec-02).
    assert can_transition("reading", "classified")  # missing_data / missing_methods / not_reproducible
    assert can_transition("acquiring_data", "classified")  # data not usable -> missing_data


def test_plan_can_be_declined_at_the_approval_gate():
    assert can_transition("plan_ready", "plan_declined")


def test_error_is_reachable_from_every_active_state():
    active = [s for s in VALIDATION_STUDY_STATES if s not in VALIDATION_STUDY_TERMINAL_STATES]
    for state in active:
        assert can_transition(state, "error"), f"{state} should be able to fail into error"


def test_classifications_are_the_six_buckets():
    assert VALIDATION_STUDY_CLASSIFICATIONS == [
        "validated",
        "not_validated",
        "missing_data",
        "missing_methods",
        "not_reproducible",
        "inconclusive",
    ]
