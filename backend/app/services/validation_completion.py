"""change_7.1 section 7: choose the outcome and its wording from the evidence.

Study 32 was refused the DEPOSIT route because the EGA deposit publishes no processed matrix, and
was then classified ``access_restricted``. Controlled access is what blocks the PIPELINE route; the
deposit route was blocked by a missing input. One label was standing in for every kind of blockage.

Its stored reason went further: "No pre-processed data to reproduce the finding from is published
for this paper" is a claim about the PAPER, and the same run had already discovered the paper's
194-row results table. A limitation must name the resource and operation it actually affects.

**Processed results and a reproduction input are different things.** A differential-results table
establishes that the authors published processed results; only a sample-level matrix establishes an
input something could reproduce from. Reporting the second as the first is what produced a
no-processed-results conclusion about a paper that published one.

**More than one limitation can be true at once**, and forcing them into a single explanation loses
the ones a reader would act on.
"""

from __future__ import annotations

CONTROLLED_ACCESS = "controlled_access"
MISSING_INPUT = "missing_input"
UNSUPPORTED_ACQUISITION = "unsupported_acquisition"
FAILED_DISCOVERY = "failed_discovery"
# change_7.3 section 5: bioAF tried to fetch something and failed. Distinct from a discovery that
# never established anything and from an input that is established as missing: it is a limitation of
# THIS attempt, and it forbids concluding that the thing is absent.
RETRIEVAL_FAILED = "retrieval_failed"

# change_7.4 section 1.1: what failed, named where it failed. Study 37 downloaded and read its matrix,
# could not place its columns, and was reported as "could not reach the deposit". None of these is an
# established absence, so none of them can produce `missing_data`.
ACCESS_REFUSED = "access_refused"
RESOURCE_LIMIT = "resource_limit"
INPUT_UNREADABLE = "input_unreadable"
UNSUPPORTED_PROCESSING = "unsupported_processing"
INPUT_UNIDENTIFIED = "input_unidentified"
SAMPLE_MAPPING_UNRESOLVED = "sample_mapping_unresolved"
DESIGN_INCOMPATIBLE = "design_incompatible"
NO_COMPATIBLE_CONTRAST = "no_compatible_contrast"
_NOT_AN_ABSENCE = (
    ACCESS_REFUSED,
    RESOURCE_LIMIT,
    INPUT_UNREADABLE,
    UNSUPPORTED_PROCESSING,
    INPUT_UNIDENTIFIED,
    SAMPLE_MAPPING_UNRESOLVED,
    DESIGN_INCOMPATIBLE,
    NO_COMPATIBLE_CONTRAST,
)

LIMITATION_KINDS = (
    CONTROLLED_ACCESS,
    UNSUPPORTED_ACQUISITION,
    MISSING_INPUT,
    FAILED_DISCOVERY,
    RETRIEVAL_FAILED,
    *_NOT_AN_ABSENCE,
)

# What each blockage means for the terminal verdict. `inconclusive` is deliberate for a discovery or
# retrieval failure: nothing was established, so nothing about the paper may be concluded.
_CLASSIFICATION_FOR = {
    CONTROLLED_ACCESS: "access_restricted",
    UNSUPPORTED_ACQUISITION: "access_restricted",
    MISSING_INPUT: "missing_data",
    FAILED_DISCOVERY: "inconclusive",
    RETRIEVAL_FAILED: "inconclusive",
    **{kind: "inconclusive" for kind in _NOT_AN_ABSENCE},
}

# The tri-state for the two completion facts. `not_established` is what a failed download yields;
# a boolean could only say "No", which is an absence nobody established.
YES = "yes"
NO = "no"
NOT_ESTABLISHED = "not_established"

# What each route needs a deposit to hold.
_ROUTE_NEEDS = {
    "deposit": ("preprocessed_data", "pre-processed data to reproduce the finding from"),
    "pipeline": ("raw_data", "raw sequencing reads to fetch and re-run"),
}
_LEGS = ("deposit", "pipeline")


# change_7.2 section 1: one vocabulary for a refusal, wherever it arose. A limitation discovered at
# acquisition time is the same kind of fact as one discovered at the gate, and a report that names
# them differently makes a reader work out that they are the same thing.
KIND_FOR_ACTION = {
    "no_adapter": UNSUPPORTED_ACQUISITION,
    "not_authorized": CONTROLLED_ACCESS,
    "no_input": MISSING_INPUT,
    "undetermined": FAILED_DISCOVERY,
}


def classification_for(limitations: list[dict]) -> str:
    """The terminal bucket a set of limitations produces."""
    return _classification(limitations)


def completion_for(
    *,
    route: str,
    capabilities: dict,
    supplements: list[dict] | None,
    extra_limitations: list[dict] | None = None,
    manifest_known: bool = True,
) -> dict:
    """The terminal outcome for a study whose route(s) cannot run.

    Returns the classification, a reader-facing reason, every limitation with the resource it
    affects, and what was and was not checked.

    change_7.3 section 5: **every leg is evaluated and only the chosen legs govern.** Assessing a leg
    costs nothing and touches only public evidence, and a deposit-route study that never states what
    the raw-read route faces cannot say the one thing a reader needs about a controlled deposit. The
    legs that were not chosen are returned as ``other_legs``: context, never run, never classifying.

    ``manifest_known`` is False when nothing listed the paper's attachments (a pasted body carries
    no manifest), so an empty inventory cannot be read as "the paper attached nothing".
    """
    deposits = [d for d in (capabilities.get("deposits") or []) if isinstance(d, dict)]
    rows = [s for s in (supplements or []) if isinstance(s, dict)]
    resolved = [s for s in rows if s.get("resolved")]
    results_tables = [s for s in resolved if s.get("role") == "results_table"]

    from app.services.validation_route_policy import legs_for

    chosen = legs_for(route)
    limitations: list[dict] = []
    other_legs: list[dict] = []
    for leg in _LEGS:
        found = _leg_limitations(leg, capabilities, deposits, results_tables)
        (limitations if leg in chosen else other_legs).extend(found)

    # A limitation the caller established for itself: an acquisition that ran out of attempts, or a
    # deposit that turned out to hold nothing usable. Discovery answered "yes" for both, so nothing
    # above can derive them, and dropping them would report a route as refused for no stated reason.
    for extra in extra_limitations or []:
        if isinstance(extra, dict) and extra.get("kind"):
            limitations.append(extra)

    # change_7.3 section 5: what the paper attached and bioAF could not inspect. It is the reason an
    # absence cannot be established, so it has to stand beside the limitation it qualifies.
    limitations.extend(_attachment_limitations(rows, manifest_known=manifest_known))

    # change_7.1 section 7 and change_7.3 section 3: a retrieved file the classifier could not place
    # is still a retrieved file. Figures and index pages are the article's packaging, not checks.
    checks_completed = [
        f"{s.get('label')}: {_CHECK_DESCRIPTION.get(s.get('role'), 'retrieved; role not established')}"
        for s in resolved
        if s.get("kind") not in ("figure", "index")
    ]

    processed, processed_reason = _processed_results(rows, results_tables, manifest_known=manifest_known)
    acquired, acquired_reason = _reproduction_input(rows, deposits, manifest_known=manifest_known)

    return {
        "classification": _classification(limitations),
        "reason": " ".join(limitation["detail"] for limitation in limitations) or "no route could be taken",
        "limitations": limitations,
        "other_legs": other_legs,
        # Two different facts, and collapsing them is what produced a no-processed-results
        # conclusion about a paper that published a results table. Each is yes, no or
        # not_established, with the reason beside it.
        "processed_results_available": processed,
        "processed_results_reason": processed_reason,
        "reproduction_input_available": acquired,
        "reproduction_input_reason": acquired_reason,
        "checks_completed": checks_completed,
        "checks_not_completed": _checks_not_completed(rows),
    }


def _leg_limitations(leg: str, capabilities: dict, deposits: list[dict], results_tables: list[dict]) -> list[dict]:
    """What stands in the way of one leg, asking the adapter question before the input question."""
    key, description = _ROUTE_NEEDS[leg]
    answer = (capabilities.get(key) or {}).get("value")

    if answer not in ("yes", "no"):
        return [
            {
                "kind": FAILED_DISCOVERY,
                "resource": "this paper's deposits",
                "operation": leg,
                "detail": (capabilities.get(key) or {}).get("failure_reason")
                or f"bioAF could not establish whether {description} is published",
            }
        ]

    if answer == "yes":
        holders = [d for d in deposits if d.get(key) == "yes"]
        acquirable = [d for d in holders if d.get("supported") == "yes" and d.get("access") != "controlled"]
        if not holders or acquirable:
            return []
        # change_7.2 section 1: the adapter question is asked FIRST, exactly as the route policy
        # asks it, so the outcome and the refusal cannot name different axes for the same fact.
        return [
            _unsupported(deposit, leg, key, description, holds=True)
            if deposit.get("supported") != "yes"
            else {
                "kind": CONTROLLED_ACCESS,
                "resource": deposit.get("accession"),
                "operation": leg,
                "detail": (
                    f"{deposit.get('accession')} publishes {description} under {deposit.get('access')} "
                    "access, and this organisation is not authorised to reach it"
                ),
            }
            for deposit in holders
        ]

    # answer == "no". change_7.3 section 5: the adapter question still comes first. `no_input` means
    # the adapter exists and the resource holds nothing that could serve; for a deposit in an archive
    # bioAF cannot read, the refusal is the missing adapter and the listing is an observation beside
    # it. Commit b7cc32f3 reordered adapter against access and never touched this path.
    note = (
        f". The paper does publish processed results ({results_tables[0].get('label')}), which can be checked "
        "for consistency but cannot be reproduced from"
        if results_tables and leg == "deposit"
        else ""
    )
    if not deposits:
        return [
            {
                "kind": MISSING_INPUT,
                "resource": "this paper's deposits",
                "operation": leg,
                "detail": f"this paper names no deposit holding {description}{note}",
            }
        ]
    found: list[dict] = []
    for deposit in deposits:
        if deposit.get("supported") != "yes":
            found.append(_unsupported(deposit, leg, key, description, holds=False))
            continue
        # Scoped to the deposit, never to the paper: the paper may well publish this elsewhere, and
        # study 32 said it did not while holding the file that proved otherwise.
        detail = f"{deposit.get('accession')} publishes no {description}{note}"
        found.append({"kind": MISSING_INPUT, "resource": deposit.get("accession"), "operation": leg, "detail": detail})
    if note and found and found[0]["kind"] == UNSUPPORTED_ACQUISITION:
        found[0]["detail"] += note
    return found


def _unsupported(deposit: dict, leg: str, key: str, description: str, *, holds: bool) -> dict:
    """A deposit in an archive bioAF has no adapter for. The limitation is bioAF's; what the public
    listing shows is carried beside it as an observation, never as the refusal."""
    accession = deposit.get("accession")
    archive = str(deposit.get("archive") or "that archive").upper()
    observation = (deposit.get("evidence_by_key") or {}).get(key) or deposit.get("evidence")
    if holds:
        detail = (
            f"{accession} publishes {description} under {deposit.get('access')} access, and bioAF has no "
            f"adapter for {archive}. The data is published; the limitation is bioAF's"
        )
    else:
        detail = (
            f"{accession} is in {archive} ({deposit.get('access')} access), and bioAF has no adapter for "
            f"{archive}, so it cannot acquire anything from it. Its public listing holds no {description}"
        )
    return {
        "kind": UNSUPPORTED_ACQUISITION,
        "resource": accession,
        "operation": leg,
        "detail": detail,
        "observation": observation,
    }


def _retrieval_status(row: dict) -> str:
    """A row's retrieval status, reading a row recorded before the ledger as it was: failed if it
    carries a copied reason, otherwise not attempted."""
    status = (row.get("retrieval") or {}).get("status")
    if status:
        return status
    if row.get("resolved"):
        return "retrieved"
    return "failed" if row.get("failure_reason") else "not_attempted"


def _candidates(rows: list[dict]) -> list[dict]:
    """The rows that could hold an input: attachments, and citations the manifest could not place.
    Index pages and figure images are packaging."""
    return [r for r in rows if r.get("kind", "attachment") not in ("index", "figure")]


def _uninspected(rows: list[dict]) -> list[dict]:
    """Candidates whose content nobody established: retrieval failed, was never attempted, or the
    bundle did not carry them. A citation with no match anywhere is a discovery limitation and is
    counted here too, because what it names is not established either."""
    return [r for r in _candidates(rows) if not r.get("resolved")]


def _attachment_limitations(rows: list[dict], *, manifest_known: bool) -> list[dict]:
    failed = [r for r in _uninspected(rows) if _retrieval_status(r) == "failed"]
    never = [r for r in _uninspected(rows) if _retrieval_status(r) == "not_attempted"]
    found: list[dict] = []
    if failed:
        found.append(
            {
                "kind": RETRIEVAL_FAILED,
                "resource": "the paper's supplementary files",
                "operation": "retrieval",
                "detail": (
                    f"bioAF could not retrieve {len(failed)} of the paper's attachment(s) in this attempt, "
                    "so what they hold is not established"
                ),
            }
        )
    if never:
        found.append(
            {
                "kind": FAILED_DISCOVERY,
                "resource": "the paper's supplementary files",
                "operation": "retrieval",
                "detail": (
                    f"bioAF did not retrieve {len(never)} of the paper's attachment(s), so what they hold is not "
                    "established"
                ),
            }
        )
    if not manifest_known and not rows:
        found.append(
            {
                "kind": FAILED_DISCOVERY,
                "resource": "the paper's supplementary files",
                "operation": "retrieval",
                "detail": "bioAF has no list of this paper's attachments, so what the paper attaches is not established",
            }
        )
    return found


def _processed_results(rows: list[dict], results_tables: list[dict], *, manifest_known: bool) -> tuple[str, str]:
    if results_tables:
        return YES, f"{results_tables[0].get('label')} is a published results table that bioAF inspected"
    uninspected = _uninspected(rows)
    if uninspected:
        return NOT_ESTABLISHED, (
            f"{len(uninspected)} of the paper's attachment(s) were not inspected, so whether the paper publishes "
            "processed results is not established"
        )
    if not manifest_known and not rows:
        return NOT_ESTABLISHED, "bioAF has no list of this paper's attachments"
    return NO, "bioAF inspected every attachment it found and none is a results table"


def _reproduction_input(rows: list[dict], deposits: list[dict], *, manifest_known: bool) -> tuple[str, str]:
    """Whether bioAF acquired a sample-level input. Acquisition is what this fact describes."""
    matrices = [r for r in rows if r.get("resolved") and r.get("role") == "expression_matrix"]
    if matrices:
        return YES, f"bioAF retrieved {matrices[0].get('label')}, a sample-level matrix"
    uninspected = _uninspected(rows)
    if uninspected:
        return NOT_ESTABLISHED, (
            f"{len(uninspected)} of the paper's attachment(s) were not inspected, so whether the paper attaches a "
            "usable input is not established; bioAF acquired none in this attempt"
        )
    if not manifest_known and not rows:
        return NOT_ESTABLISHED, "bioAF has no list of this paper's attachments; it acquired no input in this attempt"
    unreachable = [
        d
        for d in deposits
        if (d.get("raw_data") == "yes" or d.get("preprocessed_data") == "yes") and d.get("supported") != "yes"
    ]
    if unreachable:
        names = ", ".join(str(d.get("accession")) for d in unreachable)
        return NO, f"{names} holds a published input that bioAF cannot acquire, and no attachment holds one"
    return NO, "no attachment bioAF inspected holds a sample-level input, and it acquired none"


def _checks_not_completed(rows: list[dict]) -> list[str]:
    """One line per failure, never one per artifact. Study 34 repeated one bundle failure eight times
    in this list alone."""
    groups: dict[str, list[str]] = {}
    texts: dict[str, str] = {}
    for row in _uninspected(rows):
        status = _retrieval_status(row)
        if status == "failed":
            key = f"failed:{(row.get('retrieval') or {}).get('ledger') or row.get('failure_reason') or ''}"
            texts[key] = "could not be retrieved in this attempt"
        elif status == "not_in_bundle":
            key = f"absent:{row.get('label')}"
            texts[key] = (
                "named in the text; no matching attachment in the article's manifest or its bundle"
                if row.get("kind") == "reference"
                else "not found in the paper's supplementary bundle"
            )
        elif row.get("kind") == "reference":
            key = f"reference:{row.get('label')}"
            texts[key] = "named in the text; no matching attachment in the article's manifest"
        else:
            key = f"never:{row.get('label')}"
            texts[key] = "not retrieved"
        groups.setdefault(key, []).append(str(row.get("label")))
    return [f"{', '.join(labels)}: {texts[key]}" for key, labels in groups.items()]


# What inspecting a resource of each role actually established. Consistency against a published
# results table is NOT reproduction, and the wording keeps them apart.
_CHECK_DESCRIPTION = {
    "sample_metadata": "sample metadata read",
    "results_table": "published results read, available for consistency checking",
    "code": "author code retrieved and inspected",
    "expression_matrix": "sample-level matrix read",
    "supporting_input": "supporting input read",
}


def _classification(limitations: list[dict]) -> str:
    """The bucket for the limitations found.

    Access beats a missing input when both are true: a reader deciding what to do next can request
    access, and cannot conjure a matrix the authors never deposited.

    change_7.3 section 5: **`missing_data` needs an established absence.** A retrieval that failed or
    an attachment nobody inspected means the absence is not established, and the study is
    `inconclusive` rather than a finding that the paper lacks data.
    """
    kinds = {limitation["kind"] for limitation in limitations}
    for kind in (CONTROLLED_ACCESS, UNSUPPORTED_ACQUISITION):
        if kind in kinds:
            return _CLASSIFICATION_FOR[kind]
    if kinds & {RETRIEVAL_FAILED, FAILED_DISCOVERY, *_NOT_AN_ABSENCE}:
        return "inconclusive"
    if MISSING_INPUT in kinds:
        return "missing_data"
    return "inconclusive"
