"""change_7.2 section 3: acquisition has three outcomes, and every one of them has a way out.

`_hold_deposit` produced one behaviour for three different situations: record a reason, log it, and
return False with no transition and no backoff. Study 33 repeated the same listing every 30 seconds
from 10:31:40 until somebody stopped it by hand. Study 29 has been in that loop since 2026-09-07 and
is blocked by an empty accession, not by its archive. Neither `/decline` (which needs `plan_ready`)
nor `/classify` (which needs `comparing`) could stop either one, so both were parked in `error` by a
direct database write. That should never be the answer.

**Transient** is a failure to look: bounded retries with backoff, then the assessment completes
carrying an unresolved discovery limitation. The evidence conclusion stays `undetermined`, because
nothing was established; the workflow does not stay open. An exhausted discovery must never harden
into an inferred absence.

**Terminal** is one of the three typed refusals from section 1, and it says which.

**Awaiting input** is a person's turn: a visible waiting state, logged once rather than on every
tick, with a supported action.

change_7.4 section 1.1: **the caller says what failed.** Study 37 listed, downloaded and inspected
its deposit, then found no column for an arm of the design. That sentence matched none of the
signatures below, so it was read as transient, retried three times in two minutes and reported as
"bioAF could not reach the deposit". Where a hold is raised the kind of failure is known, so it is
passed as a typed cause. Reading the wording survives only for the exception a retrieval call
raised, where "unrecognised means transient" is still the right rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import validation_completion as completion
from app.services.validation_route_policy import NO_ADAPTER, NO_INPUT, NOT_AUTHORIZED, UNDETERMINED

TRANSIENT = "transient"
TERMINAL = "terminal"
AWAITING_INPUT = "awaiting_input"

# Bounded, and never a fixed 30-second interval. Three attempts over roughly a quarter of an hour is
# enough to ride out an archive's blip and short enough that an exhausted study reports promptly.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (60, 300, 900)

# ---- the typed causes a caller passes (change_7.4 section 1.1) ------------------------------------

# The archive or storage did not answer. The only failure another attempt could fix.
RETRIEVAL_TRANSIENT = "retrieval_transient"
# 404 or 410 at one location. Retried within the same bound: study 34's 404 was transient.
RETRIEVAL_NOT_FOUND = "retrieval_not_found"
# 401, 403 or 407 answered to bioAF's automated request. Not an archive's access model.
ACCESS_REFUSED = completion.ACCESS_REFUSED
# The input exceeds a limit of bioAF's own: size, row count or time.
RESOURCE_LIMIT = completion.RESOURCE_LIMIT
# Retrieved, and could not be decoded or parsed, or its value type could not be identified.
INPUT_UNREADABLE = completion.INPUT_UNREADABLE
# Retrieved or listed, and of a kind bioAF cannot yet analyze.
UNSUPPORTED_PROCESSING = completion.UNSUPPORTED_PROCESSING
# Selection could not identify a compatible input. A decision from filenames never establishes an absence.
INPUT_UNIDENTIFIED = completion.INPUT_UNIDENTIFIED
# Retrieved and read, and the evidence cannot assign the selected contrast's arms to its columns.
SAMPLE_MAPPING_UNRESOLVED = completion.SAMPLE_MAPPING_UNRESOLVED
# The evidence resolves the columns, and they do not hold the selected contrast's conditions.
DESIGN_INCOMPATIBLE = completion.DESIGN_INCOMPATIBLE
# No contrast can be analyzed on this route.
NO_COMPATIBLE_CONTRAST = completion.NO_COMPATIBLE_CONTRAST
# `no_input`, `no_adapter` and `not_authorized` are the route policy's own actions, passed as causes
# unchanged. `no_input` is reserved for an absence established within a stated scope, and
# `not_authorized` for an archive's declared access model; neither is ever read from a status code.

RETRIEVAL_CAUSES = (RETRIEVAL_TRANSIENT, RETRIEVAL_NOT_FOUND)

# What each cause is reported as when it ends the attempt.
_LIMITATION_FOR_CAUSE = {
    RETRIEVAL_TRANSIENT: completion.RETRIEVAL_FAILED,
    RETRIEVAL_NOT_FOUND: completion.RETRIEVAL_FAILED,
    ACCESS_REFUSED: completion.ACCESS_REFUSED,
    RESOURCE_LIMIT: completion.RESOURCE_LIMIT,
    INPUT_UNREADABLE: completion.INPUT_UNREADABLE,
    UNSUPPORTED_PROCESSING: completion.UNSUPPORTED_PROCESSING,
    INPUT_UNIDENTIFIED: completion.INPUT_UNIDENTIFIED,
    SAMPLE_MAPPING_UNRESOLVED: completion.SAMPLE_MAPPING_UNRESOLVED,
    DESIGN_INCOMPATIBLE: completion.DESIGN_INCOMPATIBLE,
    NO_COMPATIBLE_CONTRAST: completion.NO_COMPATIBLE_CONTRAST,
    NO_INPUT: completion.MISSING_INPUT,
    NO_ADAPTER: completion.UNSUPPORTED_ACQUISITION,
    NOT_AUTHORIZED: completion.CONTROLLED_ACCESS,
}
# The three causes that are the route policy's own actions keep that name on the record.
_POLICY_ACTIONS = (NO_INPUT, NO_ADAPTER, NOT_AUTHORIZED)


@dataclass(frozen=True)
class AcquisitionOutcome:
    """What an acquisition failure actually was, and therefore what happens next."""

    kind: str
    reason: str
    # The section 1 action this maps to, so one vocabulary describes a refusal wherever it arose.
    action: str = UNDETERMINED
    # change_7.4 section 1.1: what failed, as the caller that raised the hold knew it.
    cause: str | None = None

    @property
    def is_transient(self) -> bool:
        return self.kind == TRANSIENT

    @property
    def is_terminal(self) -> bool:
        return self.kind == TERMINAL

    @property
    def limitation_kind(self) -> str:
        """The limitation this outcome is reported as when it ends the attempt."""
        if self.cause in _LIMITATION_FOR_CAUSE:
            return _LIMITATION_FOR_CAUSE[self.cause]
        return completion.KIND_FOR_ACTION.get(self.action, completion.FAILED_DISCOVERY)


def outcome_for(cause: str, reason: str, *, archive: str | None = None) -> AcquisitionOutcome:
    """What a typed cause means for this attempt: retried, terminal, or a person's turn.

    Only a failure to retrieve is retried. Everything that failed after retrieval succeeded failed
    on something another attempt cannot change, and retrying it spends the attempts and then
    reports a retrieval failure about a file bioAF already holds.
    """
    text = (reason or "").strip()
    if cause == AWAITING_INPUT:
        return AcquisitionOutcome(kind=AWAITING_INPUT, action=UNDETERMINED, reason=text, cause=cause)
    if cause in RETRIEVAL_CAUSES:
        return AcquisitionOutcome(kind=TRANSIENT, action=UNDETERMINED, reason=text, cause=cause)
    if cause == NO_ADAPTER and archive:
        text = (
            f"bioAF has no adapter for {archive.upper()}, so it cannot acquire this deposit. "
            "The data is published; the limitation is bioAF's."
        )
    action = cause if cause in _POLICY_ACTIONS else UNDETERMINED
    return AcquisitionOutcome(kind=TERMINAL, action=action, reason=text, cause=cause)


def exhaustion_detail(
    cause: str | None,
    *,
    attempts: int,
    resource: str,
    reason: str | None = None,
    location: str | None = None,
) -> str:
    """The sentence for a retrieval that ran out of attempts, built from what failed.

    "Could not reach" is written for a failure to reach, and only for that. A 404 names where it was
    looked for, and neither one says the thing is absent.
    """
    if cause == RETRIEVAL_NOT_FOUND:
        return (
            f"{resource} was not found at {location or 'the location bioAF was given'} in {attempts} attempts, so "
            "what it holds is not established"
        )
    detail = f"bioAF could not reach {resource} after {attempts} attempts, so what it holds was never established"
    return f"{detail} ({reason})" if reason else detail


# Status codes a retrieval can answer with, read for what they are.
_NOT_FOUND_STATUSES = (404, 410)
_REFUSED_STATUSES = (401, 403, 407)
_TOO_LARGE_STATUSES = (413,)
_NOT_FOUND_TEXT = re.compile(r"(?<!\d)(404|410)(?!\d)|not found")
_REFUSED_TEXT = re.compile(r"(?<!\d)(401|403|407)(?!\d)|forbidden|unauthori[sz]ed|not authori[sz]ed|permission denied")


def retrieval_cause(error: BaseException | str | None) -> str:
    """The cause of a failed retrieval call, from what it raised.

    A status code is read where the error carries one. Otherwise the text is read, and anything
    unrecognised stays transient: calling an outage an absence is a wrong and terminal verdict.
    """
    from app.services.deposit_acquisition import DepositTooLargeError

    if isinstance(error, DepositTooLargeError):
        return RESOURCE_LIMIT
    status = _status_of(error)
    if status is not None:
        if status in _NOT_FOUND_STATUSES:
            return RETRIEVAL_NOT_FOUND
        if status in _REFUSED_STATUSES:
            return ACCESS_REFUSED
        if status in _TOO_LARGE_STATUSES:
            return RESOURCE_LIMIT
        return RETRIEVAL_TRANSIENT
    text = str(error or "").lower()
    if _REFUSED_TEXT.search(text):
        return ACCESS_REFUSED
    if _NOT_FOUND_TEXT.search(text):
        return RETRIEVAL_NOT_FOUND
    if "too large" in text:
        return RESOURCE_LIMIT
    return RETRIEVAL_TRANSIENT


def _status_of(error) -> int | None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


# A failure to reach the archive. Anything unrecognised is treated as transient, because calling an
# outage an absence is a wrong and terminal verdict on the paper.
_TRANSIENT_SIGNATURES = (
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "try again",
    "rate limit",
    "too many requests",
    "502",
    "503",
    "504",
    "server error",
    "network",
    "unreachable",
    "reset by peer",
)

# bioAF cannot perform this operation for this archive or format. A limit of ours.
_NO_ADAPTER_SIGNATURES = (
    "no adapter",
    "not a geo series id",
    "bioaf cannot acquire",
    "unsupported archive",
    "cannot be listed",
    "no download path",
)

# A refused request. change_7.4 section 1.1: a status code or its wording never establishes an
# archive's access model, so none of these is `not_authorized`; that comes only from the archive's
# own metadata.
_ACCESS_REFUSED_SIGNATURES = (
    "not authorized",
    "not authorised",
    "unauthorized",
    "unauthorised",
    "forbidden",
    "403",
    "access committee",
    "credential",
    "permission denied",
)

# An empty listing is an established absence within that listing's scope.
_NO_INPUT_SIGNATURES = ("no supplementary files",)

# A selection from filenames that found nothing it could use. Never an absence.
_UNIDENTIFIED_SIGNATURES = (
    "none of which",
    "no deposited matrix",
    "holds nothing",
    "worth reproducing from",
)

_UNREADABLE_SIGNATURES = (
    "could not be read",
    "not usable",
)

_RESOURCE_LIMIT_SIGNATURES = ("too large",)

_AWAITING_SIGNATURES = (
    "a person",
    "waiting for a choice",
    "select a file",
)


def classify_hold(reason: str | None, *, archive: str | None = None) -> AcquisitionOutcome:
    """Which situation a hold is, read from its wording.

    change_7.4 section 1.1: a caller that raises a hold passes its cause, and this reading survives
    for a hold raised with retrieval text and no cause, and for records written before causes
    existed. Anything unrecognised stays transient, never an absence.

    The archive is consulted first when it is known, because "bioAF has no adapter for EGA" is a
    fact about the registry and does not depend on how a downstream error phrased itself. Study 33
    was refused with "the accession is not a GEO series id", which is the fallback wording of an
    empty-string lookup rather than a statement about EGA.
    """
    text = (reason or "").strip()
    lowered = text.lower()

    if archive:
        from app.services.archive_discovery import can_acquire_from

        if not can_acquire_from(archive):
            return outcome_for(NO_ADAPTER, text, archive=archive)

    if _matches(lowered, _TRANSIENT_SIGNATURES):
        return outcome_for(RETRIEVAL_TRANSIENT, text)
    if _matches(lowered, _ACCESS_REFUSED_SIGNATURES):
        return outcome_for(ACCESS_REFUSED, text)
    if _matches(lowered, _NO_ADAPTER_SIGNATURES):
        return outcome_for(NO_ADAPTER, text)
    if _matches(lowered, _AWAITING_SIGNATURES):
        return outcome_for(AWAITING_INPUT, text)
    if _matches(lowered, _NO_INPUT_SIGNATURES):
        return outcome_for(NO_INPUT, text)
    if _matches(lowered, _UNIDENTIFIED_SIGNATURES):
        return outcome_for(INPUT_UNIDENTIFIED, text)
    if _matches(lowered, _UNREADABLE_SIGNATURES):
        return outcome_for(INPUT_UNREADABLE, text)
    if _matches(lowered, _RESOURCE_LIMIT_SIGNATURES):
        return outcome_for(RESOURCE_LIMIT, text)

    # Unrecognised. A failure to look, retried and then reported as unresolved, never inferred into
    # an absence.
    return outcome_for(RETRIEVAL_TRANSIENT, text)


def _matches(text: str, signatures: tuple[str, ...]) -> bool:
    return any(sig in text for sig in signatures)


def backoff_for(attempt: int) -> int:
    """Seconds to wait before attempt number ``attempt`` (1-based)."""
    index = max(0, min(attempt - 1, len(BACKOFF_SECONDS) - 1))
    return BACKOFF_SECONDS[index]


def exhausted(attempts: int) -> bool:
    return attempts >= MAX_ATTEMPTS


_ACCESSION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,}$")


def acquisition_accession(named: list[dict] | None, *, prefer_archive: str | None = None) -> dict | None:
    """Which accession an acquisition attempt should actually be pointed at.

    `_choose_from_deposit` read ``study.source_accession``, which is NULL on a study requested by
    DOI. `list_deposit` therefore received an empty string and answered "the accession is not a GEO
    series id", which is the `or 'the accession'` fallback rather than a statement about EGA.

    The requested accession keeps its authority over what a run fetches; an extracted one is used
    only when nothing was requested. ``prefer_archive`` picks among extracted accessions when the
    route needs a particular archive.
    """
    from app.services.archive_discovery import classify_archive, is_sample_accession

    candidates = [
        dict(n) for n in (named or []) if isinstance(n, dict) and _ACCESSION_RE.match(str(n.get("accession") or ""))
    ]
    for candidate in candidates:
        candidate["archive"] = classify_archive(candidate["accession"])

    usable = [c for c in candidates if not is_sample_accession(c["accession"])]
    if not usable:
        return None

    requested = [c for c in usable if c.get("provenance") == "requested"]
    if requested:
        return requested[0]
    if prefer_archive:
        matching = [c for c in usable if c["archive"] == prefer_archive]
        if matching:
            return matching[0]
    return usable[0]
