"""ValidationStudy aggregate: states, transitions, and classifications (lit_validation A1).

The ValidationStudy is the aggregate root for one validation attempt against a paper. This module
defines its state machine (mirroring the ``*_STATUS_TRANSITIONS`` convention used by Experiment and
Sample) and the terminal classification buckets. The ORM model class is added alongside these in a
later increment; the transition map is kept here so the spine's control flow can be unit-tested and
reused without touching persistence.

See ``local/lit_validation/spec-02-data-model.md`` (state machine) and ``spec-03-classification.md``
(the six buckets).
"""

# Ordered for readability: the happy path top to bottom, terminals last.
VALIDATION_STUDY_STATES = [
    "requested",
    "acquiring_text",
    "reading",
    "plan_ready",
    "acquiring_data",
    "setup",
    "running",
    "extracting",
    "comparing",
    "classified",     # terminal: carries a classification
    "plan_declined",  # terminal: human rejected the plan at the C1 gate
    "error",          # terminal: infra failure, not a judgment on the paper (retryable)
]

VALIDATION_STUDY_TERMINAL_STATES = {"classified", "plan_declined", "error"}

# Allowed forward transitions. `error` is reachable from every active state (any step can hit an
# infra failure); the early exits to `classified` let a study reach a verdict before it ever runs
# (no data, thin methods, no nf-core equivalent, or data that turns out unusable at fetch time).
VALIDATION_STUDY_TRANSITIONS: dict[str, list[str]] = {
    "requested": ["acquiring_text", "error"],
    "acquiring_text": ["reading", "error"],
    "reading": ["plan_ready", "classified", "error"],
    "plan_ready": ["acquiring_data", "plan_declined", "error"],
    "acquiring_data": ["setup", "classified", "error"],
    "setup": ["running", "error"],
    "running": ["extracting", "error"],
    "extracting": ["comparing", "error"],
    "comparing": ["classified", "error"],
    "classified": [],
    "plan_declined": [],
    "error": [],
}

# Terminal classification buckets (spec-03). The classifier states facts; there is no "bad" label.
VALIDATION_STUDY_CLASSIFICATIONS = [
    "validated",
    "not_validated",
    "missing_data",
    "missing_methods",
    "not_reproducible",
    "inconclusive",
]


def next_states(state: str) -> list[str]:
    """The states reachable from ``state`` in one transition (empty for terminals/unknowns)."""
    return VALIDATION_STUDY_TRANSITIONS.get(state, [])


def can_transition(from_state: str, to_state: str) -> bool:
    """Whether ``from_state -> to_state`` is an allowed transition."""
    return to_state in next_states(from_state)


def is_terminal(state: str) -> bool:
    """Whether ``state`` is a terminal state (no outbound transitions)."""
    return state in VALIDATION_STUDY_TERMINAL_STATES
