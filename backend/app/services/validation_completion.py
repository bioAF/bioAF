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

# What each blockage means for the terminal verdict. `inconclusive` is deliberate for a discovery
# failure: nothing was established, so nothing about the paper may be concluded.
_CLASSIFICATION_FOR = {
    CONTROLLED_ACCESS: "access_restricted",
    UNSUPPORTED_ACQUISITION: "access_restricted",
    MISSING_INPUT: "missing_data",
    FAILED_DISCOVERY: "inconclusive",
}

# What each route needs a deposit to hold.
_ROUTE_NEEDS = {
    "deposit": ("preprocessed_data", "pre-processed data to reproduce the finding from"),
    "pipeline": ("raw_data", "raw sequencing reads to fetch and re-run"),
}


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
    *, route: str, capabilities: dict, supplements: list[dict] | None, extra_limitations: list[dict] | None = None
) -> dict:
    """The terminal outcome for a study whose route(s) cannot run.

    Returns the classification, a reader-facing reason, every limitation with the resource it
    affects, and what was and was not checked.
    """
    deposits = [d for d in (capabilities.get("deposits") or []) if isinstance(d, dict)]
    resolved = [s for s in (supplements or []) if isinstance(s, dict) and s.get("resolved")]

    results_tables = [s for s in resolved if s.get("role") == "results_table"]
    matrices = [s for s in resolved if s.get("role") == "expression_matrix"]

    limitations: list[dict] = []
    for leg in ("deposit", "pipeline") if route == "both" else (route,):
        need = _ROUTE_NEEDS.get(leg)
        if need is None:
            continue
        key, description = need
        answer = (capabilities.get(key) or {}).get("value")

        if answer == "unknown":
            limitations.append(
                {
                    "kind": FAILED_DISCOVERY,
                    "resource": "this paper's deposits",
                    "operation": leg,
                    "detail": (capabilities.get(key) or {}).get("failure_reason")
                    or f"bioAF could not establish whether {description} is published",
                }
            )
            continue

        holders = [d for d in deposits if d.get(key) == "yes"]
        acquirable = [d for d in holders if d.get("supported") == "yes" and d.get("access") != "controlled"]
        if answer == "yes" and holders and not acquirable:
            for deposit in holders:
                # change_7.2 section 1: the adapter question is asked FIRST, exactly as the route
                # policy asks it, so the outcome and the refusal cannot name different axes for the
                # same fact. Both can be true of one deposit, and the adapter is the axis bioAF owns:
                # telling a lab to negotiate data access for a capability gap sends it to the wrong
                # remedy, and the reverse files a feature request for a permission problem.
                unsupported = deposit.get("supported") != "yes"
                limitations.append(
                    {
                        "kind": UNSUPPORTED_ACQUISITION if unsupported else CONTROLLED_ACCESS,
                        "resource": deposit.get("accession"),
                        "operation": leg,
                        "detail": (
                            f"{deposit.get('accession')} publishes {description} under "
                            f"{deposit.get('access')} access, and bioAF has no adapter for "
                            f"{str(deposit.get('archive') or '').upper()}. The data is published; "
                            "the limitation is bioAF's"
                            if unsupported
                            else (
                                f"{deposit.get('accession')} publishes {description} under "
                                f"{deposit.get('access')} access, and this organisation is not "
                                "authorised to reach it"
                            )
                        ),
                    }
                )
            continue

        if answer == "no":
            # Scoped to the deposit, never to the paper: the paper may well publish this elsewhere,
            # and study 32 said it did not while holding the file that proved otherwise.
            named = ", ".join(str(d.get("accession")) for d in deposits) or "the deposits bioAF found"
            detail = f"{named} publishes no {description}"
            if results_tables and leg == "deposit":
                detail += (
                    f". The paper does publish processed results ({results_tables[0].get('label')}), "
                    "which can be checked for consistency but cannot be reproduced from"
                )
            limitations.append({"kind": MISSING_INPUT, "resource": named, "operation": leg, "detail": detail})

    # A limitation the caller established for itself: an acquisition that ran out of attempts, or a
    # deposit that turned out to hold nothing usable. Discovery answered "yes" for both, so nothing
    # above can derive them, and dropping them would report a route as refused for no stated reason.
    for extra in extra_limitations or []:
        if isinstance(extra, dict) and extra.get("kind"):
            limitations.append(extra)

    # change_7.3 section 3: a retrieved file the classifier could not place is still a retrieved file.
    # Dropping it made a downloaded document vanish from the report. Figures and index pages are the
    # article's own packaging, not attachments, and are not checks.
    checks_completed = [
        f"{s.get('label')}: {_CHECK_DESCRIPTION.get(s.get('role'), 'retrieved; role not established')}"
        for s in resolved
        if s.get("kind") not in ("figure", "index")
    ]
    checks_not_completed = [
        f"{s.get('label')}: {s.get('failure_reason') or 'not retrieved'}"
        for s in (supplements or [])
        if isinstance(s, dict) and not s.get("resolved")
    ]

    return {
        "classification": _classification(limitations),
        "reason": " ".join(limitation["detail"] for limitation in limitations) or "no route could be taken",
        "limitations": limitations,
        # Two different facts, and collapsing them is what produced a no-processed-results
        # conclusion about a paper that published a results table.
        "processed_results_available": bool(results_tables),
        "reproduction_input_available": bool(matrices),
        "checks_completed": checks_completed,
        "checks_not_completed": checks_not_completed,
    }


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
    """
    kinds = {limitation["kind"] for limitation in limitations}
    for kind in (CONTROLLED_ACCESS, UNSUPPORTED_ACQUISITION, MISSING_INPUT, FAILED_DISCOVERY):
        if kind in kinds:
            return _CLASSIFICATION_FOR[kind]
    return "inconclusive"
