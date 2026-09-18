"""plan_8_3 section 0.4: every unresolved outcome bioAF can emit, and the control that resolves it.

plan_8_3's first implementation added refusals whose only outcome was unresolved and whose controls were
recorded as "not built, on purpose": an unresolved filter magnitude and an unresolved biological unit
identity were dead ends, and study 55 and study 56 stopped on them with nothing a person could do. A
gate with no control is not a completed path, however good its tests are.

This is the registry, in the shape stage 6's decision audit already uses. It is keyed by the reason
KINDS the implementation declares, walked from the modules that declare them
(``tests/test_unresolved_outcomes.py``), and each entry says:

- what the outcome says, and which module emits it;
- whether it is an UNRESOLVED outcome (something is missing, and a control can supply it) or an
  ESTABLISHED FACT (this is the answer, and there is nothing to resolve);
- for an unresolved one, the control, the module that builds it, the symbol it is reached through, and
  whether it is deterministic.

A control is deterministic wherever it can be: a recorded confirmation is a person stating a fact and
needs no model call, so it stays available when provider access does not.
"""

from __future__ import annotations

from dataclasses import dataclass

# An outcome that is not waiting for anything: the answer is that bioAF's methods do not cover this, or
# that the paper published nothing of the kind. There is no control, and the entry says why.
ESTABLISHED_FACT = "established_fact"

# The controls, named once. Each is built; the coverage test imports every module and symbol below.
_TABLE_CONFIRMATION = (
    "a recorded confirmation of how the table reads",
    "app.services.validation_table_confirmations",
    "confirmation_entry",
)
_FILTER_SEMANTICS = (
    "a recorded confirmation of the refinement's magnitude reading",
    "app.services.validation_table_confirmations",
    "confirmation_entry",
)
_UNIT_CONFIRMATION = (
    "a recorded confirmation of each column's biological unit",
    "app.services.validation_unit_confirmations",
    "confirmation_entry",
)
_RECOVERY = (
    "the study's recovery, re-read under bioAF's current rules",
    "app.services.validation_recovery",
    "run_recovery",
)
_RESUME = (
    "Review and resume, which re-enters the held step with the same input",
    "app.services.validation_study_service",
    "ValidationStudyService",
)
_RETRY_QUEUE = (
    "the queue's own bounded retry, then the study's recovery",
    "app.services.validation_check_queue",
    "retry_later",
)
_DEPOSIT_PICK = (
    "a person's choice of which deposited file to use",
    "app.services.validation_study_service",
    "ValidationStudyService",
)
_READ_AGAIN = (
    "asking the failed step again, with the paper's text supplied where bioAF did not keep it",
    "app.services.validation_driver_service",
    "ValidationDriverService",
)
_ADMIN_ACCOUNT = (
    "an administrator putting the model account right",
    "app.services.llm_provider_clients",
    "account_fact",
)
_ADMIN_PROVIDER = (
    "an administrator restoring bioAF's access to the provider",
    "app.services.llm_provider_config_service",
    "get_for_feature",
)
_ACCESS_AGREEMENT = (
    "the data access agreement or credentials the archive requires, recorded for the organization",
    "app.services.validation_route_policy",
    "decide_route",
)


@dataclass(frozen=True)
class UnresolvedOutcome:
    """One reason kind, and what would resolve it."""

    kind: str
    vocabulary: str
    what: str
    emitted_by: str
    control: str
    control_module: str = ""
    control_symbol: str = ""
    deterministic: bool = False
    why_no_control: str = ""


def _entry(kind: str, vocabulary: str, what: str, emitted_by: str, control, *, deterministic: bool = False):
    words, module, symbol = control
    return UnresolvedOutcome(
        kind=kind,
        vocabulary=vocabulary,
        what=what,
        emitted_by=emitted_by,
        control=words,
        control_module=module,
        control_symbol=symbol,
        deterministic=deterministic,
    )


def _fact(kind: str, vocabulary: str, what: str, emitted_by: str, why: str):
    return UnresolvedOutcome(
        kind=kind,
        vocabulary=vocabulary,
        what=what,
        emitted_by=emitted_by,
        control=ESTABLISHED_FACT,
        why_no_control=why,
    )


_ACQ = "acquisition_cause"
_TERMINAL = "check_terminal_reason"
_SUPPORT = "support_state"
_TABLE = "author_table_state"
_DECISION = "decision_outcome"
_CHECK = "check_outcome"

_ENTRIES = (
    # ---- the typed causes a hold carries (validation_acquisition_outcome) -------------------------
    _entry(
        "retrieval_transient",
        _ACQ,
        "the archive or storage did not answer",
        "app/services/validation_acquisition_outcome.py",
        _RETRY_QUEUE,
    ),
    _entry(
        "retrieval_not_found",
        _ACQ,
        "the file was not at the location bioAF asked for",
        "app/services/validation_acquisition_outcome.py",
        _RETRY_QUEUE,
    ),
    _entry(
        "access_refused",
        _ACQ,
        "the archive refused bioAF's automated request",
        "app/services/validation_acquisition_outcome.py",
        _ACCESS_AGREEMENT,
    ),
    _entry(
        "resource_limit",
        _ACQ,
        "the input is past a limit of bioAF's own: its size, its rows or its time",
        "app/services/validation_acquisition_outcome.py",
        _DEPOSIT_PICK,
    ),
    _entry(
        "input_unreadable",
        _ACQ,
        "the input arrived and could not be decoded, parsed or measured",
        "app/services/validation_acquisition_outcome.py",
        _DEPOSIT_PICK,
    ),
    _entry(
        "input_unidentified",
        _ACQ,
        "which deposited file to use is not established",
        "app/services/validation_acquisition_outcome.py",
        _DEPOSIT_PICK,
    ),
    _entry(
        "sample_mapping_unresolved",
        _ACQ,
        "the evidence does not assign the contrast's arms to the input's columns",
        "app/services/validation_input_choice.py",
        _RESUME,
        deterministic=True,
    ),
    _entry(
        "biological_identity_unresolved",
        _ACQ,
        "the arms are settled and which biological unit each column came from is not",
        "app/services/validation_input_choice.py",
        _UNIT_CONFIRMATION,
        deterministic=True,
    ),
    _entry(
        "design_incompatible",
        _ACQ,
        "the columns are resolved and they do not hold the contrast's conditions",
        "app/services/deposit_metadata_association.py",
        _DEPOSIT_PICK,
    ),
    _fact(
        "no_compatible_contrast",
        _ACQ,
        "no contrast of the paper can be analyzed on this route",
        "app/services/contrast_selection.py",
        "the route itself is the answer; the other route is chosen at the approval gate, not by resolving this",
    ),
    _fact(
        "unsupported_processing",
        _ACQ,
        "what the authors deposited is of a kind bioAF cannot yet analyze",
        "app/services/validation_acquisition_outcome.py",
        "a capability bioAF does not have is a limit to state, never something a person can supply",
    ),
    _fact(
        "no_input",
        _ACQ,
        "within the scope bioAF stated, the paper published no input of the kind the route needs",
        "app/services/validation_route_policy.py",
        "an established absence is the answer; nothing resolves it, and a later deposit is a new attempt",
    ),
    _fact(
        "no_adapter",
        _ACQ,
        "the archive the paper names is one bioAF has no adapter for",
        "app/services/validation_route_policy.py",
        "a capability bioAF does not have is a limit to state",
    ),
    _fact(
        "not_authorized",
        _ACQ,
        "the archive's declared access model does not allow an automated request",
        "app/services/validation_route_policy.py",
        "the archive's own access model is the fact; credentials are the ACCESS_REFUSED case, not this",
    ),
    # ---- why a check stopped for good (validation_check_queue) ------------------------------------
    _entry(
        "binding",
        _TERMINAL,
        "which table reports this claim's contrast is not established",
        "app/services/validation_table_binding.py",
        _TABLE_CONFIRMATION,
        deterministic=True,
    ),
    _entry(
        "interpretation",
        _TERMINAL,
        "what arrived could not be interpreted: its encoding, its format or its columns",
        "app/services/table_decoding.py",
        _TABLE_CONFIRMATION,
        deterministic=True,
    ),
    _entry(
        "retries_exhausted",
        _TERMINAL,
        "the table could not be retrieved within the attempts bioAF allows",
        "app/services/validation_check_queue.py",
        _RECOVERY,
    ),
    _entry(
        "persistence_failed",
        _TERMINAL,
        "the check ran and its result could not be recorded",
        "app/services/validation_consistency_checks.py",
        _RECOVERY,
    ),
    _entry(
        "error",
        _TERMINAL,
        "the pass failed outside any one check, and each due check spent one attempt of its bound",
        "app/services/validation_consistency_checks.py",
        _RECOVERY,
    ),
    _entry(
        "limit",
        _TERMINAL,
        "the check would pass a limit bioAF holds for checks run before approval",
        "app/services/validation_consistency_checks.py",
        _RECOVERY,
    ),
    _entry(
        "access_refused_check",
        _TERMINAL,
        "the archive refused bioAF's request for the authors' table",
        "app/services/validation_consistency_checks.py",
        _ACCESS_AGREEMENT,
    ),
    _entry(
        "unavailable",
        _TERMINAL,
        "bioAF holds no copy of the table and no address to fetch it from",
        "app/services/validation_consistency_checks.py",
        _RECOVERY,
    ),
    # ---- the check outcomes plan_8_3 added -------------------------------------------------------
    _entry(
        "filter_magnitude_unresolved",
        _CHECK,
        "the refinement's words do not say whether its cutoff is on the magnitude of the effect or on "
        "its signed value, and the two select different genes",
        "app/services/validation_published_subset.py",
        _FILTER_SEMANTICS,
        deterministic=True,
    ),
    _entry(
        "parent_not_established",
        _CHECK,
        "the table is not established as the claim's complete published selected list",
        "app/services/validation_table_binding.py",
        _TABLE_CONFIRMATION,
        deterministic=True,
    ),
    # ---- per-experiment support (validation_applicability) ---------------------------------------
    _fact(
        "supported",
        _SUPPORT,
        "bioAF's current methods cover this experiment",
        "app/services/validation_applicability.py",
        "support is not an unresolved outcome",
    ),
    _entry(
        "awaiting_input",
        _SUPPORT,
        "the method is implemented and the input it needs is not in hand",
        "app/services/validation_applicability.py",
        _DEPOSIT_PICK,
    ),
    _entry(
        "unresolved_interpretation",
        _SUPPORT,
        "what the experiment measured is matched only by a contextual marker, which settles nothing",
        "app/services/validation_applicability.py",
        _RECOVERY,
    ),
    _entry(
        "failed_decision",
        _SUPPORT,
        "a model decision the support rests on failed, so support is not established either way",
        "app/services/validation_applicability.py",
        _RECOVERY,
    ),
    _fact(
        "unsupported",
        _SUPPORT,
        "no evidence and check combination bioAF implements applies to this experiment",
        "app/services/validation_applicability.py",
        "a capability bioAF does not have is a limit to state, and it never means the paper holds no "
        "quantitative analysis",
    ),
    # ---- whether an author result table is available (validation_author_table_state) --------------
    _fact(
        "available",
        _TABLE,
        "a results table bioAF can read is in hand",
        "app/services/validation_author_table_state.py",
        "availability is not an unresolved outcome",
    ),
    _entry(
        "not_inspected",
        _TABLE,
        "something the paper published has not been inspected yet",
        "app/services/validation_author_table_state.py",
        _RECOVERY,
    ),
    _entry(
        "retrieval_failed",
        _TABLE,
        "bioAF's own download of the authors' results did not succeed",
        "app/services/validation_author_table_state.py",
        _RECOVERY,
    ),
    _entry(
        "unsupported_format",
        _TABLE,
        "what the authors published is in a format bioAF cannot read as a table",
        "app/services/validation_author_table_state.py",
        _TABLE_CONFIRMATION,
        deterministic=True,
    ),
    _entry(
        "no_eligible_table",
        _TABLE,
        "nothing bioAF inspected is a results table for this claim",
        "app/services/validation_author_table_state.py",
        _RECOVERY,
    ),
    _entry(
        "unresolved_applicability",
        _TABLE,
        "a table is in hand and whether it reports this claim is not established",
        "app/services/validation_author_table_state.py",
        _TABLE_CONFIRMATION,
        deterministic=True,
    ),
    _fact(
        "not_deposited",
        _TABLE,
        "the paper names no supplement at all",
        "app/services/validation_author_table_state.py",
        "an established absence is the answer; it is reachable only when the paper names nothing",
    ),
    # ---- what a model decision reported (llm_decision) -------------------------------------------
    _entry(
        "refusal",
        _DECISION,
        "the model declined to answer",
        "app/services/llm_decision.py",
        _ADMIN_ACCOUNT,
    ),
    _entry(
        "unreachable",
        _DECISION,
        "bioAF could not reach the provider",
        "app/services/llm_decision.py",
        _ADMIN_PROVIDER,
    ),
    _entry(
        "account",
        _DECISION,
        "the account bioAF uses cannot run the request: its credit, its quota or its entitlement",
        "app/services/llm_decision.py",
        _ADMIN_ACCOUNT,
    ),
    _entry(
        "internal",
        _DECISION,
        "bioAF hit an error of its own while asking",
        "app/services/llm_decision.py",
        _RECOVERY,
    ),
    _entry(
        "unparseable",
        _DECISION,
        "the answer was not in the format bioAF asked for",
        "app/services/llm_decision.py",
        _READ_AGAIN,
    ),
    _entry(
        "truncated",
        _DECISION,
        "the answer was cut off at its token limit before it finished",
        "app/services/llm_decision.py",
        _READ_AGAIN,
    ),
    _entry(
        "timed_out",
        _DECISION,
        "the answer did not finish within bioAF's time limit",
        "app/services/llm_decision.py",
        _READ_AGAIN,
    ),
    _entry(
        "schema_rejected",
        _DECISION,
        "the answer arrived whole, twice, and did not hold what bioAF asked for",
        "app/services/llm_decision.py",
        _READ_AGAIN,
    ),
)

UNRESOLVED_OUTCOMES: dict[str, UnresolvedOutcome] = {e.kind: e for e in _ENTRIES}


def vocabularies() -> dict[str, tuple[str, ...]]:
    """The sets the implementation itself declares, read from where they are declared.

    The registry is walked against these, so a member added to one of them fails the coverage test
    rather than quietly becoming an outcome with nothing that resolves it. ``access_refused`` is a
    cause AND a check's terminal reason, and the check's entry is keyed ``access_refused_check`` so
    each keeps its own words; ``check_outcome`` holds what plan_8_3's own operations emit.
    """
    from app.services import llm_decision
    from app.services import validation_acquisition_outcome as acquisition
    from app.services import validation_applicability as applicability
    from app.services import validation_author_table_state as table_state
    from app.services import validation_check_queue as queue

    return {
        _ACQ: tuple(acquisition._LIMITATION_FOR_CAUSE),
        _TERMINAL: tuple(f"{r}_check" if r in acquisition._LIMITATION_FOR_CAUSE else r for r in queue.TERMINAL_REASONS),
        _SUPPORT: tuple(applicability.SUPPORT_STATES),
        _TABLE: tuple(table_state.STATES),
        _DECISION: tuple(o for o in llm_decision.OUTCOMES if o != llm_decision.OUTCOME_OK),
        _CHECK: ("filter_magnitude_unresolved", "parent_not_established"),
    }


def controls_for(kinds) -> list[dict]:
    """What would resolve each of ``kinds`` that is resolvable, in the order given.

    A surface offering a person the way out of a held study reads this, so the offer and the registry
    cannot disagree about what exists.
    """
    found = []
    for kind in kinds or []:
        entry = UNRESOLVED_OUTCOMES.get(str(kind))
        if entry is None or entry.control == ESTABLISHED_FACT:
            continue
        found.append(
            {
                "kind": entry.kind,
                "what": entry.what,
                "control": entry.control,
                "deterministic": entry.deterministic,
            }
        )
    return found
