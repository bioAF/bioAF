"""change_7.3 section 11: one projection of a study's evidence, for the page and both exports.

The UI and the markdown renderer each kept their own label maps and their own logic, so every
correction to the report had to be made twice and drifted: `_LIMITATION_LABEL` and
`_SUPPLEMENT_ROLE_LABEL` in the renderer duplicated `CompletionSummary.tsx` and
`SupplementInventory.tsx`. This module is the one place a study's report is worded from.

**It projects facts and never decides them.** Identity, classification and completion are decided
when evidence is written and persisted there. This groups, selects wording, and derives whether
reproduction was attempted from execution evidence already recorded. That is what keeps the persisted
evidence, the page, the JSON export and the markdown export saying the same things.

**Legacy studies read correctly.** A study recorded before the retrieval ledger carries a copied
``failure_reason`` on each unretrieved row. That is read as a retrieval failure whose technical detail
is only what was recorded, identical reasons are grouped, and the persisted classification is shown as
recorded. "Review and resume" re-derives it on the current build.

It is a pure function: the same inputs always produce the same projection, and nothing here reads the
database or the network.
"""

from __future__ import annotations

from app.services.validation_reproduction_attempt import ATTEMPTED, reproduction_attempt

# ---- the vocabularies, each worded once --------------------------------------------------------------

LIMITATION_LABELS = {
    "controlled_access": "Controlled access",
    "unsupported_acquisition": "Acquisition not supported",
    "missing_input": "Required input not published",
    "failed_discovery": "Could not be established",
    "retrieval_failed": "Could not be retrieved in this attempt",
    # change_7.4 section 1.1. The plan's proposed labels, pending the owner's sign-off item by item.
    "access_refused": "Access refused to bioAF's automated request",
    "resource_limit": "Input exceeds bioAF's processing limits",
    "input_unreadable": "Acquired input could not be read",
    "unsupported_processing": "bioAF cannot yet analyze this kind of file",
    "input_unidentified": "No compatible input identified",
    "sample_mapping_unresolved": "Samples could not be assigned to the comparison",
    "design_incompatible": "Acquired input does not contain the compared conditions",
    "no_compatible_contrast": "No comparison this route can analyze",
}
# A missing input the evidence has not established is not a missing input. Worded as what it is.
_UNESTABLISHED_ABSENCE_LABEL = "Not established"

ROLE_LABELS = {
    "sample_metadata": "Sample metadata",
    "expression_matrix": "Expression matrix",
    "results_table": "Differential results",
    "code": "Analysis code",
    "supporting_input": "Supporting input",
    "unknown": "Retrieved; role not established",
}

RETRIEVAL_LABELS = {
    "not_attempted": "Not retrieved",
    "failed": "Could not be retrieved in this attempt",
    "retrieved": "Retrieved",
    "not_in_bundle": "Not found in the paper's supplementary bundle",
}

RETRIEVAL_OUTCOME_LABELS = {
    "retrieved": "retrieved",
    "not_found": "the server answered that it was not found",
    "forbidden": "the server refused the request",
    "transient": "the request failed before an answer came back",
    "too_large": "it is larger than bioAF will download",
    "unreadable": "it arrived but could not be opened",
}

IDENTIFICATION_LABELS = {
    "article_manifest": "the article's list of attachments",
    "prose": "the paper's text",
    "methods": "the methods",
    "bundle": "the supplementary bundle",
}

ISSUE_OUTCOME_LABELS = {
    "refusal": "the model declined to answer",
    "unreachable": "bioAF could not reach the language model",
    "internal": "bioAF hit an internal error",
    "unparseable": "the model's answer was not in the format bioAF asked for",
    "truncated": "the model's answer was cut off at its token limit",
    "retrieval_failed": "bioAF could not retrieve a file it needed",
    "not_performed": "this step could not run",
}

TRISTATE_LABELS = {"yes": "Yes", "no": "No", "not_established": "Not established", "unknown": "Unknown"}

CHECK_LABELS = {
    "species_matches": "Species matches the deposit",
    "sample_data_matches_paper": "Sample data matches the paper",
    "methods_detailed_enough": "Methods described in enough detail",
    "samples_described_enough": "Samples described in enough detail",
}

EXECUTION_LABELS = {
    "not_attempted": "Not executed",
    "attempted": "Executed",
}

# Section 10, items 1 and 2: the headline follows the attempt, never the bucket alone.
HEADLINE_NOT_ATTEMPTED = "reproduction_not_attempted"
HEADLINE_COULD_NOT_REPRODUCE = "could_not_reproduce"
HEADLINE_VERDICT = "verdict"
HEADLINE_IN_PROGRESS = "in_progress"
HEADLINE_LABELS = {
    HEADLINE_NOT_ATTEMPTED: "Reproduction not attempted",
    HEADLINE_COULD_NOT_REPRODUCE: "Could Not Reproduce",
}
_VERDICT_CLASSIFICATIONS = ("validated", "partially_reproduced", "not_validated")

PROVISIONAL_NOTE = "from the paper text only; not checked against the attachments"
NO_SUPPORTED_METRIC_EXPLANATION = (
    "bioAF has no metric that measures this claim, so the mapping was declined and the claim cannot be compared."
)

_NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}


def enum_labels() -> dict[str, dict[str, str]]:
    """Every vocabulary a report renders, for the cross-stack contract test."""
    return {
        "limitation_kind": dict(LIMITATION_LABELS),
        "role": dict(ROLE_LABELS),
        "retrieval_status": dict(RETRIEVAL_LABELS),
        "retrieval_outcome": dict(RETRIEVAL_OUTCOME_LABELS),
        "issue_outcome": dict(ISSUE_OUTCOME_LABELS),
        "tristate": dict(TRISTATE_LABELS),
        "headline": dict(HEADLINE_LABELS),
    }


# ---- the projection -----------------------------------------------------------------------------------


def summarize(
    *,
    study: dict,
    evidence: dict | None,
    plan: dict | None,
    targets: list[dict] | None,
    issues: list[dict] | None,
) -> dict:
    """The report, as one set of statements every surface renders.

    ``study`` carries ``state``, ``classification``, ``analysis_run_id`` and ``data_run_id``.
    """
    evidence = evidence or {}
    plan = plan or {}
    targets = [t for t in targets or [] if isinstance(t, dict)]
    attempt = reproduction_attempt(
        evidence, analysis_run_id=study.get("analysis_run_id"), data_run_id=study.get("data_run_id")
    )
    artifacts, figures, index_pages = _artifacts(evidence)
    failures = _retrieval_failures(evidence, artifacts)
    completion = evidence.get("completion") or {}
    uninspected = any(a["inspection"]["status"] != "inspected" for a in artifacts)
    limitations = _limitations(completion, uninspected=uninspected)
    claims, counts = _claims(targets, plan, evidence)
    reconciliation = _reconciliation(evidence)
    headline = _headline(study, attempt)
    completion_facts = _completion_facts(completion, evidence, study, uninspected=uninspected)
    acquired = any(f["key"] == "input_acquired" and f["value"] == "yes" for f in completion_facts)
    facts = _facts(evidence, artifacts, attempt, counts, acquired=acquired)

    return {
        "version": 1,
        "attempt": attempt,
        "headline": headline,
        "summary": _summary_lines(headline, facts),
        "facts": facts,
        "limitations": limitations,
        "retrieval_failures": failures,
        "artifacts": artifacts,
        "figures": figures,
        "index_pages": index_pages,
        "code_sources": _code_sources(evidence),
        "capability_rows": _capability_rows(evidence),
        "claims": claims,
        "claim_counts": counts,
        "blockers": _blockers(plan, evidence),
        "contrasts": _contrasts(plan, evidence),
        "reconciliation": reconciliation,
        "consistency": _consistency(evidence),
        "checks": _checks(evidence, reconciliation),
        "completion_facts": completion_facts,
        "checks_completed": [str(c) for c in completion.get("checks_completed") or []],
        # Grouped from the artifacts, so a study recorded before the ledger (eight lines, one copied
        # failure each) reads as the one failure it was.
        "checks_not_completed": _checks_not_completed(artifacts),
        "comparisons": _comparisons(attempt, counts),
        "resume": _resume(limitations, failures),
        "issue_count": len(issues or []),
    }


def _basis(evidence: dict) -> str:
    """What every reading from the paper rests on: the assessment's basis once reconciliation has run
    on inspected evidence, the paper's text until then."""
    return (evidence.get("assessment") or {}).get("basis") or "paper_text"


def _blockers(plan: dict, evidence: dict) -> list[dict]:
    """Section 6: a blocker is a reading of the prose, provisional until inspected evidence settles it."""
    basis = _basis(evidence)
    kinds = {str(k.get("text")): k.get("kind") for k in plan.get("blocker_kinds") or [] if isinstance(k, dict)}
    return [
        {"text": str(b), "kind": kinds.get(str(b)), "basis": basis, "provisional": basis != "inspected_evidence"}
        for b in plan.get("blockers") or []
    ]


def _contrasts(plan: dict, evidence: dict) -> list[dict]:
    basis = _basis(evidence)
    design = plan.get("differential_design") or {}
    # change_7.4 section 1.4: only the selected contrast is validated and executed. The others stay
    # in the plan and are reported as not assessed, never as checked and found wanting.
    selected = (design.get("selected_contrast") or {}).get("contrast_index")
    return [
        {
            "name": c.get("name"),
            "thresholds": c.get("thresholds"),
            "basis": basis,
            "provisional": basis != "inspected_evidence",
            "status": "selected" if index == selected else "unassessed",
        }
        for index, c in enumerate(design.get("contrasts") or [])
        if isinstance(c, dict)
    ]


def _headline(study: dict, attempt: dict) -> dict:
    if study.get("state") != "classified":
        return {"key": HEADLINE_IN_PROGRESS, "label": None}
    if attempt["status"] != ATTEMPTED:
        return {"key": HEADLINE_NOT_ATTEMPTED, "label": HEADLINE_LABELS[HEADLINE_NOT_ATTEMPTED]}
    if study.get("classification") in _VERDICT_CLASSIFICATIONS:
        return {"key": HEADLINE_VERDICT, "label": None}
    return {"key": HEADLINE_COULD_NOT_REPRODUCE, "label": HEADLINE_LABELS[HEADLINE_COULD_NOT_REPRODUCE]}


# ---- artifacts ----------------------------------------------------------------------------------------


def _retrieval_status(row: dict) -> str:
    status = (row.get("retrieval") or {}).get("status")
    if status:
        return status
    if row.get("resolved"):
        return "retrieved"
    return "failed" if row.get("failure_reason") else "not_attempted"


def _artifacts(evidence: dict) -> tuple[list[dict], int, int]:
    """Attachments and unplaced citations with their four statuses; figures and index pages counted."""
    from app.services.supplement_inventory import establish_identity

    rows = establish_identity([s for s in evidence.get("supplements") or [] if isinstance(s, dict)])
    artifacts: list[dict] = []
    figures = index_pages = 0
    for row in rows:
        kind = row.get("kind") or "attachment"
        if kind == "figure":
            figures += 1
            continue
        if kind == "index":
            index_pages += 1
            continue
        status = _retrieval_status(row)
        inspected = bool(row.get("resolved"))
        role = row.get("role") or "unknown"
        artifacts.append(
            {
                "identity": row.get("identity"),
                "label": row.get("label") or row.get("filename") or "attachment",
                "filename": row.get("filename"),
                "kind": kind,
                "references": list(row.get("references") or []),
                "identification": list(row.get("identified_in") or []),
                "identification_label": ", ".join(
                    IDENTIFICATION_LABELS.get(i, i) for i in row.get("identified_in") or []
                ),
                "retrieval": {
                    "status": status,
                    "label": (
                        "Named in the text; no matching attachment in the article's manifest"
                        if kind == "reference" and status in ("not_attempted", "not_in_bundle")
                        else RETRIEVAL_LABELS.get(status, status)
                    ),
                    "ledger": (row.get("retrieval") or {}).get("ledger"),
                    "recorded_reason": row.get("failure_reason"),
                },
                "inspection": {
                    "status": "inspected" if inspected else "not_inspected",
                    "role": role if inspected else None,
                    "role_label": ROLE_LABELS.get(role, role) if inspected else None,
                    "measurements": _measurements(row) if inspected else [],
                },
            }
        )
    return artifacts, figures, index_pages


def _measurements(row: dict) -> list[str]:
    """Row counts only for an inspected TABLE: a document has no rows, and a missing key is not a
    count (section 10 item 3: `row_count !== null` rendered "undefined rows")."""
    parts: list[str] = []
    if isinstance(row.get("row_count"), int):
        parts.append(f"{row['row_count']} rows")
    for name, count in (row.get("threshold_splits") or {}).items():
        parts.append(f"{count} with {name}")
    return parts


def _retrieval_failures(evidence: dict, artifacts: list[dict]) -> list[dict]:
    """One entry per failed source, never one per artifact (section 10 item 4)."""
    ledger = [e for e in evidence.get("retrieval_ledger") or [] if isinstance(e, dict)]
    by_id = {e.get("id"): e for e in ledger}
    groups: dict[str, list[dict]] = {}
    for artifact in artifacts:
        if artifact["retrieval"]["status"] != "failed":
            continue
        key = artifact["retrieval"]["ledger"] or f"recorded:{artifact['retrieval']['recorded_reason'] or ''}"
        groups.setdefault(key, []).append(artifact)

    failures: list[dict] = []
    for key, members in groups.items():
        entry = by_id.get(key)
        if entry is not None:
            attempts = [e for e in ledger if e.get("url") == entry.get("url") and _same_sequence(e, entry, ledger)]
            technical = {
                "url": entry.get("url"),
                "http_status": entry.get("http_status"),
                "error_class": entry.get("error_class"),
                "outcome": RETRIEVAL_OUTCOME_LABELS.get(entry.get("outcome"), entry.get("outcome")),
                "attempts": len(attempts),
                "first_at": attempts[0].get("at") if attempts else entry.get("at"),
                "last_at": entry.get("at"),
            }
            source = entry.get("source_label") or "the paper's supplementary files"
        else:
            technical = {"recorded_reason": members[0]["retrieval"]["recorded_reason"]}
            source = "the article's supplementary bundle from Europe PMC"
        failures.append(
            {
                "source": source,
                "message": "bioAF could not download the paper's supplementary files in this attempt",
                "artifacts": [m["label"] for m in members],
                "technical_detail": technical,
            }
        )
    return failures


def _checks_not_completed(artifacts: list[dict]) -> list[str]:
    """One line per failure and per unplaced artifact, never one per copy of the same failure."""
    groups: dict[str, list[str]] = {}
    texts: dict[str, str] = {}
    for artifact in artifacts:
        if artifact["inspection"]["status"] == "inspected":
            continue
        status = artifact["retrieval"]["status"]
        if status == "failed":
            key = f"failed:{artifact['retrieval']['ledger'] or artifact['retrieval']['recorded_reason'] or ''}"
        else:
            key = f"{status}:{artifact['label']}"
        texts[key] = artifact["retrieval"]["label"].lower() if status == "failed" else artifact["retrieval"]["label"]
        groups.setdefault(key, []).append(artifact["label"])
    return [f"{', '.join(labels)}: {texts[key]}" for key, labels in groups.items()]


def _same_sequence(entry: dict, last: dict, ledger: list[dict]) -> bool:
    """Whether ``entry`` belongs to the attempt sequence that ended at ``last``: the run of entries
    numbered 1..n that finishes with it."""
    index = ledger.index(last)
    start = index - (int(last.get("attempt") or 1) - 1)
    return entry in ledger[max(0, start) : index + 1]


def _code_sources(evidence: dict) -> list[dict]:
    code = evidence.get("code_execution") or {}
    outcome = code.get("outcome") if isinstance(code, dict) else None
    rows = []
    for source in (evidence.get("capabilities") or {}).get("code_sources") or []:
        if not isinstance(source, dict):
            continue
        retrieval = source.get("retrieval") or {}
        inspection = source.get("inspection") or {}
        status = retrieval.get("status") or (
            "retrieved" if source.get("accessible") == "yes" else (source.get("accessible") or "not_attempted")
        )
        executed = bool(outcome) and outcome not in ("code_absent", "code_unreachable", "generation_failed")
        rows.append(
            {
                "label": source.get("identifier") or source.get("url") or source.get("kind"),
                "kind": source.get("kind"),
                "identification": list(source.get("identified_in") or []),
                "identification_label": ", ".join(
                    IDENTIFICATION_LABELS.get(i, i) for i in source.get("identified_in") or []
                ),
                "retrieval": {
                    "status": status,
                    "label": RETRIEVAL_LABELS.get(status, status),
                    "reason": source.get("accessible_reason"),
                },
                "inspection": {
                    "status": inspection.get("status") or "not_inspected",
                    "role_label": ROLE_LABELS.get(inspection.get("role")) if inspection.get("role") else None,
                },
                "execution": {
                    "status": "attempted" if executed else "not_attempted",
                    "label": EXECUTION_LABELS["attempted" if executed else "not_attempted"],
                    "outcome": outcome if executed else None,
                },
            }
        )
    return rows


# Section 10 item 11: each data row is two facts. Whether the authors deposited it is about the paper;
# whether bioAF can acquire it is about bioAF and this organization.
_DATA_ROWS = (
    ("raw_data", "Raw sample data deposited", "Raw sample data available to bioAF"),
    ("preprocessed_data", "Pre-processed data deposited", "Pre-processed data available to bioAF"),
    ("sample_metadata", "Sample metadata deposited", "Sample metadata available to bioAF"),
)
_PLAIN_ROWS = (
    ("paper_readable", "Paper text available"),
    ("deposit_exists", "Data deposit exists"),
)
_CODE_ROWS = (
    ("code_artifact", "Code artifact exists"),
    ("code_repository", "Code repository linked"),
)
_CHECKLIST_VALUE_LABELS = {"yes": "Yes", "no": "No", "unknown": "Unknown", "not_attempted": "Not attempted"}


def _row(key: str, label: str, value, detail) -> dict:
    return {
        "key": key,
        "label": label,
        "value": value,
        "value_label": _CHECKLIST_VALUE_LABELS.get(value, value),
        "detail": detail,
    }


def _capability_rows(evidence: dict) -> list[dict]:
    caps = evidence.get("capabilities") or {}
    if not caps:
        return []
    deposits = [d for d in caps.get("deposits") or [] if isinstance(d, dict)]
    rows: list[dict] = []
    for key, label in _PLAIN_ROWS:
        answer = caps.get(key)
        if isinstance(answer, dict):
            rows.append(_row(key, label, answer.get("value"), answer.get("failure_reason") or answer.get("evidence")))
    for key, deposited, available in _DATA_ROWS:
        answer = caps.get(key)
        if not isinstance(answer, dict):
            continue
        rows.append(_row(key, deposited, answer.get("value"), answer.get("failure_reason") or answer.get("evidence")))
        value, reason = answer.get("available_to_bioaf"), answer.get("available_reason")
        if value is None:
            # Recorded before availability was a field. Read from what the deposits recorded about
            # themselves, which is where the fact already lived.
            holders = [d for d in deposits if d.get(key) == "yes"]
            reachable = [d for d in holders if d.get("supported") == "yes" and d.get("access") != "controlled"]
            if reachable:
                value, reason = "yes", None
            elif holders:
                value = "no"
                reason = "; ".join(
                    f"{d.get('accession')} is {d.get('access')} access"
                    + (
                        f" and bioAF has no adapter for {str(d.get('archive') or '').upper()}"
                        if d.get("supported") != "yes"
                        else ""
                    )
                    for d in holders
                )
            elif answer.get("value") == "no":
                value, reason = "no", "nothing of this kind is deposited"
            else:
                value = "unknown"
        rows.append(_row(f"{key}_available", available, value, reason))
    for key, label in _CODE_ROWS:
        answer = caps.get(key)
        if isinstance(answer, dict):
            rows.append(_row(key, label, answer.get("value"), answer.get("failure_reason") or answer.get("evidence")))
    return rows


# ---- limitations, completion, resume -------------------------------------------------------------------


def _limitations(completion: dict, *, uninspected: bool) -> list[dict]:
    rows = []
    for governs, source in ((True, completion.get("limitations") or []), (False, completion.get("other_legs") or [])):
        for limitation in source:
            if not isinstance(limitation, dict):
                continue
            kind = limitation.get("kind")
            label = LIMITATION_LABELS.get(kind, kind)
            if kind == "missing_input" and uninspected:
                # Section 10 item 12: the missing-input label applies only when the absence is
                # established. A study recorded before that rule is read under it.
                label = _UNESTABLISHED_ABSENCE_LABEL
            rows.append(
                {
                    "kind": kind,
                    "label": label,
                    "resource": limitation.get("resource"),
                    "leg": limitation.get("operation"),
                    "detail": limitation.get("detail"),
                    "observation": limitation.get("observation"),
                    "governs": governs,
                }
            )
    return rows


def _tristate(value, *, uninspected: bool) -> str:
    if isinstance(value, bool):
        # A study recorded before the tri-state: a False beside an uninspected attachment was an
        # absence nobody established.
        return "yes" if value else ("not_established" if uninspected else "no")
    return value if value in TRISTATE_LABELS else "not_established"


# change_7.4 section 1.3. The plan's proposed labels, pending the owner's sign-off item by item.
INPUT_FACT_LABELS = {
    "input_acquired": "Analysis input acquired",
    "input_usable": "Analysis input usable",
    "ready_for_analysis": "Ready for analysis",
}


def _input_facts(completion: dict, evidence: dict, study: dict) -> dict:
    """The three input facts as recorded, or, for a completion recorded before they existed, read
    from the same acquisition record the completion now reads. A legacy record said "acquired none"
    beside a file bioAF had downloaded (study 37), and no surface may repeat that."""
    from app.services.validation_completion import analysis_input_facts

    if "input_acquired" in completion:
        return completion
    return analysis_input_facts(
        evidence,
        evidence.get("supplements") or [],
        ((evidence.get("capabilities") or {}).get("deposits")) or [],
        data_run_id=study.get("data_run_id"),
    )


def _completion_facts(completion: dict, evidence: dict, study: dict, *, uninspected: bool) -> list[dict]:
    if not completion:
        return []
    legacy = "recorded before bioAF distinguished an established absence from one it could not establish"
    raw = completion.get("processed_results_available")
    value = _tristate(raw, uninspected=uninspected)
    facts = [
        {
            "key": "processed_results_available",
            "label": "Processed results published",
            "value": value,
            "value_label": TRISTATE_LABELS[value],
            "reason": completion.get("processed_results_reason")
            or (legacy if isinstance(raw, bool) and value != "yes" else None),
        }
    ]
    recorded = _input_facts(completion, evidence, study)
    from app.services.validation_completion import INPUT_FACT_KEYS

    for key, reason_key in INPUT_FACT_KEYS:
        value = recorded.get(key) if recorded.get(key) in TRISTATE_LABELS else "not_established"
        facts.append(
            {
                "key": key,
                "label": INPUT_FACT_LABELS[key],
                "value": value,
                "value_label": TRISTATE_LABELS[value],
                "reason": recorded.get(reason_key),
            }
        )
    return facts


def _resume(limitations: list[dict], failures: list[dict]) -> dict:
    """Section 10 item 10: what would unblock each limitation, generated from this study's own."""
    requirements: list[str] = []
    unsupported_archives = sorted(
        {_archive_of(limitation) for limitation in limitations if limitation["kind"] == "unsupported_acquisition"}
    )
    if unsupported_archives:
        requirements.append(
            f"bioAF cannot yet acquire data from {' or '.join(unsupported_archives)}, so credentials alone will not "
            "make this study runnable."
        )
    for limitation in limitations:
        if limitation["kind"] == "controlled_access" and limitation["governs"]:
            requirements.append(
                f"Approved access to {limitation.get('resource')} from its data access committee, entered in bioAF, "
                f"would let the {limitation.get('leg') or 'chosen'} route run."
            )
    # change_7.4 section 1.1: a deposit whose retrieval ran out of attempts is `retrieval_failed` too,
    # and it is not an attachment. Only the attachment failures promise the attachment download.
    attachment_failure = any(
        limitation["kind"] == "retrieval_failed" and limitation.get("leg") == "retrieval" for limitation in limitations
    )
    if failures or attachment_failure:
        requirements.append("Approving again retries the attachment download.")
    if any(
        limitation["governs"]
        and (
            limitation["kind"] == "failed_discovery"
            or (limitation["kind"] == "retrieval_failed" and limitation.get("leg") != "retrieval")
        )
        for limitation in limitations
    ):
        requirements.append("Approving again retries what could not be established in this attempt.")
    return {"label": "Review and resume", "requirements": list(dict.fromkeys(requirements))}


def _archive_of(limitation: dict) -> str:
    detail = str(limitation.get("detail") or "")
    for token in ("EGA", "ARRAYEXPRESS", "GEO", "SRA", "OTHER"):
        if f"adapter for {token}" in detail:
            return token
    resource = str(limitation.get("resource") or "").upper()
    if resource.startswith("EGA"):
        return "EGA"
    if resource.startswith("E-"):
        return "ARRAYEXPRESS"
    return "that archive"


# ---- claims -------------------------------------------------------------------------------------------


def _cutoff_text(target: dict) -> str | None:
    cutoffs = target.get("cutoffs") or []
    if cutoffs:
        words = {"padj": "padj", "pvalue": "p", "abs_log2fc": "|log2FC|", "fold_change": "fold change"}
        return " and ".join(
            f"{words.get(c.get('kind'), c.get('kind'))} {c.get('operator')} {c.get('value'):g}"
            for c in cutoffs
            if isinstance(c, dict) and isinstance(c.get("value"), (int, float))
        )
    if target.get("threshold") is not None:
        kind = target.get("threshold_kind")
        return f"{target['threshold']} ({kind})" if kind else str(target["threshold"])
    return None


def _tested_count(evidence: dict) -> int:
    comparisons = (evidence.get("classification_result") or {}).get("comparisons") or []
    tested = sum(1 for c in comparisons if isinstance(c, dict) and c.get("computed_value") is not None)
    if evidence.get("level3_result"):
        tested += 1
    return tested


def _claims(targets: list[dict], plan: dict, evidence: dict) -> tuple[list[dict], dict]:
    assessment = evidence.get("assessment") or {}
    basis = assessment.get("basis") or "paper_text"
    contrasts = ((plan.get("differential_design") or {}).get("contrasts")) or []
    tested = _tested_count(evidence)
    claims = []
    mapped = 0
    for target in targets:
        bound = target.get("bound_key")
        decided_by = target.get("bound_by") or "alias_table"
        if bound:
            # The key itself sits under the binding details (section 10 item 7); the row says what
            # the mapping is, in the counts line's own words.
            status, label, explanation = "mapped", "Mapped to a candidate comparison metric", None
            mapped += 1
        elif decided_by == "binding_failed":
            status, label, explanation = (
                "failed",
                "Mapping could not be made",
                "The model's answer could not be read, so this claim is not mapped to a metric.",
            )
        elif decided_by == "model":
            status, label, explanation = "no_supported_metric", "No supported metric", NO_SUPPORTED_METRIC_EXPLANATION
        else:
            status, label, explanation = "unmapped", "Not mapped", "No mapping decision was recorded for this claim."
        index = target.get("contrast_index")
        claims.append(
            {
                "description": target.get("claim_text") or str(target.get("metric_key") or "claim").replace("_", " "),
                "value": target.get("claimed_value"),
                "unit": target.get("unit"),
                "population": target.get("sample_subset"),
                "stage": target.get("qc_stage"),
                "contrast": (
                    contrasts[index].get("name") if isinstance(index, int) and 0 <= index < len(contrasts) else None
                ),
                "cutoff": _cutoff_text(target),
                "basis": basis,
                "provisional": basis != "inspected_evidence",
                "unresolved_reason": target.get("unresolved_reason"),
                "mapping": {
                    "status": status,
                    "label": label,
                    "explanation": explanation,
                    "metric_key": target.get("metric_key"),
                    "bound_key": bound,
                    "confidence": target.get("binding_confidence") if decided_by == "model" else None,
                    "model": target.get("bound_by_model") if decided_by == "model" else None,
                    "reason": target.get("binding_reason"),
                    "decided_by": decided_by,
                },
            }
        )
    counts = {"total": len(claims), "mapped": mapped, "tested": tested, "label": _counts_label(mapped, tested)}
    return claims, counts


def _counts_label(mapped: int, tested: int) -> str:
    """Section 10 item 5: mapped is not tested, and the tested count comes from evidence."""
    noun = "claim" if mapped == 1 else "claims"
    tested_text = "none tested" if tested == 0 else f"{tested} tested"
    return f"{mapped} {noun} mapped to candidate comparison metrics; {tested_text}."


# ---- reconciliation, consistency, checks ---------------------------------------------------------------


def _reconciliation(evidence: dict) -> dict:
    assessment = evidence.get("assessment") or {}
    record = assessment.get("reconciliation")
    if isinstance(record, dict) and record.get("status"):
        status, reason, basis = record["status"], record.get("reason"), record.get("basis")
    elif assessment:
        # A study recorded before reconciliation said whether it ran.
        if assessment.get("reconciled"):
            status, reason, basis = "performed", "reconciled against the inspected attachments", "inspected_evidence"
        elif not assessment.get("supplements_inspected"):
            status, reason, basis = "not_performed", "no attachments were inspected", None
        else:
            status, reason, basis = "not_performed", "the reason was not recorded", None
    else:
        return {"status": None, "reason": None, "basis": None, "label": None}
    if status == "performed":
        label = (
            "Reconciled against the inspected attachments"
            if basis == "inspected_evidence"
            else f"Reconciled against the paper's own passages; {PROVISIONAL_NOTE}"
        )
    else:
        label = f"Reconciliation not performed: {reason}"
    return {"status": status, "reason": reason, "basis": basis, "label": label}


def _consistency(evidence: dict) -> dict:
    assessment = evidence.get("assessment") or {}
    pass_record = assessment.get("contradiction_pass") or {}
    checked = bool(pass_record.get("checked"))
    unresolved = [
        {"statement": c.get("statement"), "outcome": c.get("outcome")}
        for c in assessment.get("unresolved_contradictions") or []
        if isinstance(c, dict)
    ]
    if not checked:
        label = "Consistency not checked"
    elif unresolved:
        label = f"{len(unresolved)} unresolved contradiction(s) among the checked statements"
    else:
        label = "No contradictions found among the checked statements"
    return {"checked": checked, "pairs": list(pass_record.get("pairs") or []), "label": label, "unresolved": unresolved}


def _checks(evidence: dict, reconciliation: dict) -> list[dict]:
    rows = []
    for key, check in (evidence.get("precompute_checks") or {}).items():
        if not isinstance(check, dict):
            continue
        basis = check.get("basis") or (
            "inspected_evidence" if check.get("decided_by") == "measurement" else "paper_text"
        )
        rows.append(
            {
                "key": key,
                "label": CHECK_LABELS.get(key, key),
                "verdict": check.get("verdict"),
                "detail": check.get("detail"),
                "basis": basis,
                # Section 6: no check renders as a settled "Yes" while it rests on the prose.
                "provisional": basis != "inspected_evidence",
            }
        )
    return rows


def _comparisons(attempt: dict, counts: dict) -> dict:
    if counts["tested"]:
        return {"performed": True, "label": None, "reason": None}
    reason = (
        "reproduction was not attempted, so no claim was compared"
        if attempt["status"] != ATTEMPTED
        else "the reproduction produced nothing any claim could be compared against"
    )
    return {"performed": False, "label": "Comparisons not performed", "reason": reason}


# ---- facts and the summary sentences -------------------------------------------------------------------


def _facts(evidence: dict, artifacts: list[dict], attempt: dict, counts: dict, *, acquired: bool = False) -> dict:
    caps = evidence.get("capabilities") or {}
    raw = caps.get("raw_data") or {}
    deposits = [d for d in caps.get("deposits") or [] if isinstance(d, dict)]
    holders = [d for d in deposits if d.get("raw_data") == "yes"]
    available = raw.get("available_to_bioaf")
    if available is None and holders:
        # A study recorded before availability was a field: derive nothing, read what is there.
        reachable = [d for d in holders if d.get("supported") == "yes" and d.get("access") != "controlled"]
        available = "yes" if reachable else "no"
    attachments = [a for a in artifacts if a["kind"] == "attachment"]
    return {
        "reproduction_attempted": attempt["status"] == ATTEMPTED,
        "raw_data": {
            "deposited": raw.get("value") or "unknown",
            "available_to_bioaf": available or "unknown",
            "access": sorted({str(d.get("access")) for d in holders if d.get("access")}),
        },
        "attachments": {
            "identified": len(attachments),
            "retrieved": sum(1 for a in attachments if a["retrieval"]["status"] == "retrieved"),
            "failed": sum(1 for a in attachments if a["retrieval"]["status"] == "failed"),
            "not_attempted": sum(1 for a in attachments if a["retrieval"]["status"] == "not_attempted"),
        },
        # The same acquisition record the completion facts read, so the summary sentence and the
        # "Analysis input acquired" row can never disagree.
        "inputs_acquired": bool(attempt["acquired"]) or acquired,
        "claims_tested": counts["tested"],
    }


def _count_word(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def _summary_lines(headline: dict, facts: dict) -> list[str]:
    """Sentences generated from the facts above, never from a template per paper."""
    lines: list[str] = []
    if headline["key"] == HEADLINE_NOT_ATTEMPTED:
        lines.append("Reproduction not attempted.")
    elif headline["key"] == HEADLINE_COULD_NOT_REPRODUCE:
        lines.append("Reproduction was attempted and reached no verdict.")

    raw = facts["raw_data"]
    if raw["deposited"] == "yes":
        access = " under controlled access" if "controlled" in raw["access"] else ""
        if raw["available_to_bioaf"] == "no":
            lines.append(f"Raw data are deposited{access} and were unavailable to bioAF in this attempt.")
        elif raw["available_to_bioaf"] == "yes":
            lines.append(f"Raw data are deposited{access} and available to bioAF.")
        else:
            lines.append(f"Raw data are deposited{access}; whether bioAF can acquire them was not established.")
    elif raw["deposited"] == "no":
        lines.append("The paper's deposits hold no raw sequencing reads.")
    else:
        lines.append("Whether raw data are deposited could not be established.")

    attachments = facts["attachments"]
    identified = attachments["identified"]
    if identified:
        noun = "attachment was" if identified == 1 else "attachments were"
        sentence = f"{_count_word(identified)} supplementary {noun} identified"
        if attachments["failed"] == identified:
            sentence += ", but " + ("its download" if identified == 1 else "their download") + " failed."
        elif attachments["retrieved"] == identified:
            sentence += " and retrieved."
        elif attachments["failed"]:
            sentence += (
                f"; {attachments['retrieved']} were retrieved and {attachments['failed']} could not be retrieved "
                "in this attempt."
            )
        else:
            sentence += f"; {attachments['retrieved']} were retrieved."
        lines.append(sentence)
    else:
        lines.append("No supplementary attachments were identified.")

    acquired = "No analysis inputs were acquired" if not facts["inputs_acquired"] else "Analysis inputs were acquired"
    tested = (
        "no scientific claims were tested"
        if not facts["claims_tested"]
        else f"{facts['claims_tested']} scientific claim(s) were tested"
    )
    lines.append(f"{acquired} and {tested}.")
    return lines


# ---- loading, shared by the API and the export ---------------------------------------------------------


async def report_summary_for(session, study, org_id: int) -> dict:
    """Load a study's plan, targets and issues and project them. The API response and the provenance
    export both call this, so they cannot disagree about what the report says."""
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_assessment import active_plan
    from app.services.validation_issue_service import ValidationIssueService

    plan = await active_plan(session, study)
    targets: list[dict] = []
    plan_dict: dict = {}
    if plan is not None:
        rows = (
            (
                await session.execute(
                    select(ComparisonTarget)
                    .where(ComparisonTarget.reproduction_plan_id == plan.id)
                    .order_by(ComparisonTarget.id)
                )
            )
            .scalars()
            .all()
        )
        targets = [target_dict(t) for t in rows]
        plan_dict = {
            "blockers": plan.blockers_json,
            "blocker_kinds": plan.blocker_kinds_json,
            "differential_design": plan.differential_design_json,
            "finding_claim": plan.finding_claim_json,
        }
    return summarize(
        study={
            "state": study.state,
            "classification": study.classification,
            "analysis_run_id": study.analysis_run_id,
            "data_run_id": study.data_run_id,
        },
        evidence=study.evidence_json,
        plan=plan_dict,
        targets=targets,
        issues=await ValidationIssueService.list_for_study(session, study.id, org_id),
    )


def target_dict(t) -> dict:
    """One comparison target as the projection reads it."""
    return {
        "metric_key": t.metric_key,
        "claim_text": t.claim_text,
        "claimed_value": t.claimed_value,
        "unit": t.unit,
        "source_locator": t.source_locator,
        "sample_subset": t.sample_subset,
        "qc_stage": t.qc_stage,
        "direction": t.direction,
        "threshold": t.threshold,
        "threshold_kind": t.threshold_kind,
        "output_type": t.output_type,
        "measurement_basis": t.measurement_basis,
        "contrast_index": t.contrast_index,
        "cutoffs": t.cutoffs,
        "unresolved_reason": t.unresolved_reason,
        "bound_key": t.bound_key,
        "binding_reason": t.binding_reason,
        "binding_confidence": t.binding_confidence,
        "bound_by_model": t.bound_by_model,
        "bound_by": t.bound_by,
    }
