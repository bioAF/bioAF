"""ValidationStudy aggregate: states, transitions, and classifications (lit_validation A1).

The ValidationStudy is the aggregate root for one validation attempt against a paper. This module
defines its state machine (mirroring the ``*_STATUS_TRANSITIONS`` convention used by Experiment and
Sample) and the terminal classification buckets. The ORM model class is added alongside these in a
later increment; the transition map is kept here so the spine's control flow can be unit-tested and
reused without touching persistence.

See ``local/lit_validation/spec-02-data-model.md`` (state machine) and ``spec-03-classification.md``
(the six buckets).
"""

import uuid as uuid_pkg
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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
    "reproducing",  # Level-3 (ADR-069): reproduce the paper's finding + score concordance
    "comparing",
    "classified",  # terminal: carries a classification
    "plan_declined",  # terminal: human rejected the plan at the C1 gate
    "error",  # terminal: infra failure, not a judgment on the paper (retryable)
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
    # extracting routes to reproducing when Level-3 inputs are present, else straight to comparing
    # (Level-2 only), so the existing QC-only flow is unchanged.
    "extracting": ["reproducing", "comparing", "error"],
    "reproducing": ["comparing", "error"],
    "comparing": ["classified", "error"],
    "classified": [],
    "plan_declined": [],
    "error": [],
}

# Terminal classification buckets (spec-03). The classifier states facts; there is no "bad" label.
# `partially_reproduced` sits between `validated` and `not_validated`: the paper's finding reproduced
# in part (the overlap enrichment is statistically real) but recovery was incomplete (ADR-069, E6
# `partial`). It always holds for a human.
VALIDATION_STUDY_CLASSIFICATIONS = [
    "validated",
    "partially_reproduced",
    "not_validated",
    "missing_data",
    "missing_methods",
    "not_reproducible",
    "inconclusive",
]

# Interim map: manual classification bucket -> "% confident the results were validated" for the UI
# status badge (frontend lib/validationStatus). This is a stopgap until the E2 comparison engine
# produces a real graded confidence; a manual human verdict is discrete, so it only ever yields the
# extremes. The "couldn't test / couldn't conclude" buckets (and any not-yet-classified study) map to
# None, which the UI renders as "Could Not Reproduce" -- deliberately distinct from a LOW confidence
# (could-not-test is not the same as tested-and-unlikely).
_CLASSIFICATION_CONFIDENCE: dict[str, float | None] = {
    "validated": 100.0,  # human-confirmed validation -> Fully Validated
    # partially_reproduced was tested AND concluded (the finding reproduced in part), so it is NOT a
    # "could not reproduce" None; it lands in a caution/needs-review band. The frontend renders the
    # precise "Partially Reproduced" label from the classification bucket; this number is the fallback
    # for confidence-only consumers (e.g. the provenance report).
    "partially_reproduced": 60.0,  # -> Possibly Validated (caution, needs human review)
    "not_validated": 0.0,  # human-confirmed contradiction -> Very Unlikely
    "missing_data": None,  # no data to run -> Could Not Reproduce
    "missing_methods": None,  # no reproducible method -> Could Not Reproduce
    "not_reproducible": None,  # pipeline could not run -> Could Not Reproduce
    "inconclusive": None,  # ran but no verdict -> Could Not Reproduce
}


def classification_confidence(classification: str | None) -> float | None:
    """The UI's "% confident the results were validated" for a classification, or None when validation
    could not be run/concluded or the study is not yet classified.

    Interim mapping (see the comment above): a discrete manual verdict yields only 100 / 0 / None. The
    E2 comparison engine will replace this with a real graded confidence."""
    if classification is None:
        return None
    return _CLASSIFICATION_CONFIDENCE.get(classification)


def next_states(state: str) -> list[str]:
    """The states reachable from ``state`` in one transition (empty for terminals/unknowns)."""
    return VALIDATION_STUDY_TRANSITIONS.get(state, [])


def can_transition(from_state: str, to_state: str) -> bool:
    """Whether ``from_state -> to_state`` is an allowed transition."""
    return to_state in next_states(from_state)


def is_terminal(state: str) -> bool:
    """Whether ``state`` is a terminal state (no outbound transitions)."""
    return state in VALIDATION_STUDY_TERMINAL_STATES


class ValidationStudy(Base):
    """One validation attempt for a paper (aggregate root). Org-scoped; writes are audited.

    Fields track provenance (paper/DOI/accession), the C1 approval gate, the linked experiment and
    reproduction plan, and the assembled evidence bundle. ``state`` is driven through
    ``VALIDATION_STUDY_TRANSITIONS`` by ``ValidationStudyService``; ``classification`` is null until a
    terminal ``classified`` state is reached.
    """

    __tablename__ = "validation_studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), unique=True
    )
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    # Source provenance. paper_id links the library paper when sourced from it; the DOI/accession are
    # captured even for ad-hoc papers not in the library.
    paper_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("literature_papers.id"), nullable=True, index=True
    )
    source_doi: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_accession: Mapped[str | None] = mapped_column(String(255), nullable=True)

    requested_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    state: Mapped[str] = mapped_column(String(50), nullable=False, server_default="requested")
    classification: Mapped[str | None] = mapped_column(String(50), nullable=True)

    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    # FK to reproduction_plans is added when that table lands (component B2/B3); kept as a plain
    # nullable column for now so the spine can persist without a forward table dependency.
    reproduction_plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The back-half driver (A2) links the two pipeline runs it launches for this study: the
    # nf-core/fetchngs data-acquisition run (D1) and the nf-core/rnaseq|scrnaseq analysis run (D3).
    # Plain nullable columns (like reproduction_plan_id) so the driver can correlate a completed run
    # to a study + stage without coupling the spine to pipeline_runs ordering in the test schema.
    data_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    approved_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    evidence_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization")
