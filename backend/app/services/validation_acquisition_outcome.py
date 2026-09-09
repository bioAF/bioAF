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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.validation_route_policy import NO_ADAPTER, NO_INPUT, NOT_AUTHORIZED, UNDETERMINED

TRANSIENT = "transient"
TERMINAL = "terminal"
AWAITING_INPUT = "awaiting_input"

# Bounded, and never a fixed 30-second interval. Three attempts over roughly a quarter of an hour is
# enough to ride out an archive's blip and short enough that an exhausted study reports promptly.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (60, 300, 900)


@dataclass(frozen=True)
class AcquisitionOutcome:
    """What an acquisition failure actually was, and therefore what happens next."""

    kind: str
    reason: str
    # The section 1 action this maps to, so one vocabulary describes a refusal wherever it arose.
    action: str = UNDETERMINED

    @property
    def is_transient(self) -> bool:
        return self.kind == TRANSIENT

    @property
    def is_terminal(self) -> bool:
        return self.kind == TERMINAL


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

# The adapter exists and inputs exist; this organization cannot reach them.
_NOT_AUTHORIZED_SIGNATURES = (
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

# The adapter exists; the resource holds nothing that could serve.
_NO_INPUT_SIGNATURES = (
    "none of which",
    "no deposited matrix",
    "holds nothing",
    "no supplementary files",
    "worth reproducing from",
    "could not be read",
    "not usable",
    "too large",
)

_AWAITING_SIGNATURES = (
    "a person",
    "waiting for a choice",
    "select a file",
)


def classify_hold(reason: str | None, *, archive: str | None = None) -> AcquisitionOutcome:
    """Which of the three situations this hold is.

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
            return AcquisitionOutcome(
                kind=TERMINAL,
                action=NO_ADAPTER,
                reason=(
                    f"bioAF has no adapter for {archive.upper()}, so it cannot acquire this deposit. "
                    "The data is published; the limitation is bioAF's."
                ),
            )

    if _matches(lowered, _TRANSIENT_SIGNATURES):
        return AcquisitionOutcome(kind=TRANSIENT, action=UNDETERMINED, reason=text)
    if _matches(lowered, _NOT_AUTHORIZED_SIGNATURES):
        return AcquisitionOutcome(kind=TERMINAL, action=NOT_AUTHORIZED, reason=text)
    if _matches(lowered, _NO_ADAPTER_SIGNATURES):
        return AcquisitionOutcome(kind=TERMINAL, action=NO_ADAPTER, reason=text)
    if _matches(lowered, _AWAITING_SIGNATURES):
        return AcquisitionOutcome(kind=AWAITING_INPUT, action=UNDETERMINED, reason=text)
    if _matches(lowered, _NO_INPUT_SIGNATURES):
        return AcquisitionOutcome(kind=TERMINAL, action=NO_INPUT, reason=text)

    # Unrecognised. A failure to look, retried and then reported as unresolved, never inferred into
    # an absence.
    return AcquisitionOutcome(kind=TRANSIENT, action=UNDETERMINED, reason=text)


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
