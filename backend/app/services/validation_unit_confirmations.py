"""plan_8_3 stage 5: a person's recorded confirmation of which biological unit each column came from.

Stage 5 refuses a unit identity that nothing the row cites states, and it is right to: a paper's
donors, clones and cultures cannot be read off a column's order, its trailing digits or a repeating
filename pattern, and treating a unit TYPE as an identity collapsed study 50's six columns into one
biological sample. What the first implementation left out is the way OUT of that refusal, so the gate
could only ever end unresolved (section 0.4: a gate with no control is a dead end).

Where the published sources do not establish the units, a person may record them with the evidence
they rest on. It is deterministic: a person stating a fact needs no model call, so the control stays
available when provider access does not. It is disclosed: a mapping that rests on one is assisted
(``ASSISTANCE_UNIT_IDENTITY``) and is never reported as unattended validation. And it is not a way
around the gate, because the same rules apply to what a person records: a unit that is only what KIND
of unit it is, or that is the column's own name or its trailing number, is refused here too.

The confirmation is kept on ``evidence["unit_confirmations"]`` with its version and provenance, and
every confirmation it replaces is kept beside it. Recording one does not re-run anything by itself:
the study's existing "Review and resume" re-enters mapping with the same input, and the mapping is
then validated with the recorded units in hand.
"""

from __future__ import annotations

from app.services.validation_input_choice import (
    UNIT_KIND_IDENTITY,
    _TRAILING_NUMBER,
    unit_identity,
)

VERSION = 1

# The words a refusal uses, shared with the mapping validation so a person reads the same sentence
# whoever stated the unit. Pending the owner's sign-off.
NOT_AN_IDENTITY = (
    '"{unit}" is the kind of unit it is, not which {type_word} it is; every column carrying the same '
    "words would be one unit"
)
FROM_THE_COLUMN = (
    '"{unit}" is column {column}\'s own name or its trailing number; a unit identity is never read off '
    "a sample's order, its trailing digits or a repeating filename pattern"
)


class ConfirmationRefused(ValueError):
    """The confirmation cannot be recorded; the words say why."""


def confirmation_entry(
    *,
    matrix: str | None,
    units: dict | None,
    columns: list[str] | None,
    note: str,
    confirmed_by: str | None,
    at: str,
) -> dict:
    """One recorded set of biological-unit identities, validated. Raises ``ConfirmationRefused``.

    ``columns`` are the input's own columns, so a confirmation cannot name one it does not hold.
    """
    note = (note or "").strip()
    if not note:
        raise ConfirmationRefused("a confirmation records the evidence it rests on; say what establishes it")
    stated = {str(k): str(v or "").strip() for k, v in (units or {}).items() if str(v or "").strip()}
    if not stated:
        raise ConfirmationRefused("the confirmation states no biological unit")
    held = list(columns or [])
    for column, unit in stated.items():
        if held and column not in held:
            raise ConfirmationRefused(f"the input does not hold a column named {column}")
        identity = unit_identity(unit)
        if identity["kind"] != UNIT_KIND_IDENTITY:
            raise ConfirmationRefused(NOT_AN_IDENTITY.format(unit=unit, type_word=identity["type_word"] or "one"))
        trailing = _TRAILING_NUMBER.search(column)
        if unit == column or (trailing and unit == trailing.group(1)):
            raise ConfirmationRefused(FROM_THE_COLUMN.format(unit=unit, column=column))
    return {
        "version": VERSION,
        "matrix": matrix,
        "units": stated,
        "note": note,
        "confirmed_by": confirmed_by,
        "at": at,
        "superseded": [],
    }


def latest(evidence: dict | None) -> dict | None:
    """The confirmation that applies, or None."""
    found = (evidence or {}).get("unit_confirmations")
    return found if isinstance(found, dict) and found.get("units") else None


def fingerprint_of(confirmation: dict | None) -> str | None:
    from app.services.validation_check_queue import fingerprint

    if not isinstance(confirmation, dict):
        return None
    return fingerprint({k: v for k, v in confirmation.items() if k != "superseded"})


async def record_confirmation(session, study, plan, entry: dict, *, reason: str) -> dict:
    """Keep ``entry`` on the study, with every confirmation it replaces beside it.

    The mapping is not re-validated here. A held study's existing "Review and resume" re-enters
    mapping with the same input, and that is where the recorded units are applied, so recording a
    fact and acting on it stay separate decisions.
    """
    evidence = dict(study.evidence_json or {})
    previous = evidence.get("unit_confirmations")
    superseded = list((previous or {}).get("superseded") or [])
    if isinstance(previous, dict) and previous.get("units"):
        superseded.append({k: v for k, v in previous.items() if k != "superseded"})
    current = {**entry, "superseded": superseded, "reason": reason}
    evidence["unit_confirmations"] = current
    study.evidence_json = evidence
    await session.flush()
    return current
