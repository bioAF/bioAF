"""State machine for the ValidationStudy aggregate (lit_validation A1, spec-02).

Pure unit tests of the transition map, terminal states, and early-exit paths. No DB, so these
pin the spine's control flow independently of persistence.
"""

from app.models.validation_study import (
    VALIDATION_STUDY_STATES,
    VALIDATION_STUDY_TERMINAL_STATES,
    VALIDATION_STUDY_TRANSITIONS,
    VALIDATION_STUDY_CLASSIFICATIONS,
    _CLASSIFICATION_CONFIDENCE,
    can_transition,
    classification_confidence,
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


def test_a_closed_verdict_has_no_outbound_transitions():
    """`classified` and `plan_declined` are judgments. Nothing follows them."""
    for terminal in ("classified", "plan_declined"):
        assert terminal in VALIDATION_STUDY_TERMINAL_STATES
        assert next_states(terminal) == []
        assert is_terminal(terminal)
    assert not is_terminal("reading")


def test_error_is_parked_rather_than_closed():
    """`error` is an INFRA failure, not a judgment on the paper -- the model has said "retryable" in
    a comment since it was written, while the transition table said otherwise, so a wrong reference
    or a dead node cost the whole fetch and there was no way back.

    It stays TERMINAL, which is what stops the background driver touching it: a study that failed
    must never re-launch itself on a tick, or one broken parameter becomes a spend loop. Retrying is
    a human act, and these are the two places a human can send it."""
    assert is_terminal("error")
    assert "error" in VALIDATION_STUDY_TERMINAL_STATES
    assert set(next_states("error")) == {"setup", "plan_ready"}


def test_early_exit_to_classified_before_running():
    # A study can reach a terminal classification before any pipeline runs (spec-02).
    assert can_transition("reading", "classified")  # missing_data / missing_methods / not_reproducible
    assert can_transition("acquiring_data", "classified")  # data not usable -> missing_data


def test_plan_can_be_declined_at_the_approval_gate():
    assert can_transition("plan_ready", "plan_declined")


def test_samples_mismatch_holds_before_compute_and_can_run_or_stop():
    # A picked sample that was not fetched parks the study here (zero compute) so a human decides.
    assert "samples_mismatch" in VALIDATION_STUDY_STATES
    assert can_transition("acquiring_data", "samples_mismatch")
    assert can_transition("samples_mismatch", "setup")  # "run with the samples we have" (override)
    assert can_transition("samples_mismatch", "plan_declined")  # "stop"
    assert not is_terminal("samples_mismatch")


def test_error_is_reachable_from_every_active_state():
    active = [s for s in VALIDATION_STUDY_STATES if s not in VALIDATION_STUDY_TERMINAL_STATES]
    for state in active:
        assert can_transition(state, "error"), f"{state} should be able to fail into error"


def test_classifications_are_the_seven_buckets():
    assert VALIDATION_STUDY_CLASSIFICATIONS == [
        "validated",
        "partially_reproduced",
        "not_validated",
        "missing_data",
        "missing_methods",
        "not_reproducible",
        "inconclusive",
    ]


def test_classification_confidence_interim_mapping():
    # Interim until E2: a discrete manual verdict yields only the extremes or None.
    assert classification_confidence("validated") == 100.0  # -> Fully Validated
    assert classification_confidence("not_validated") == 0.0  # -> Very Unlikely
    # partially_reproduced WAS tested and DID conclude (the finding partially reproduced), so it is not
    # None ("could not reproduce"); it lands in a caution/needs-review band. The frontend badge renders
    # the precise "Partially Reproduced" label from the classification; this number is the fallback for
    # confidence-only consumers (the provenance report).
    assert classification_confidence("partially_reproduced") == 60.0
    # "couldn't test / couldn't conclude" and not-yet-classified -> None (UI: Could Not Reproduce),
    # deliberately NOT a low confidence.
    for c in ("missing_data", "missing_methods", "not_reproducible", "inconclusive"):
        assert classification_confidence(c) is None
    assert classification_confidence(None) is None
    # Every real bucket must be mapped EXPLICITLY (no silent default-to-None on a new bucket).
    assert set(VALIDATION_STUDY_CLASSIFICATIONS) == set(_CLASSIFICATION_CONFIDENCE)
