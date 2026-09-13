"""plan_8 sections 3 and 4: each finding's outcome, normalized from the evidence the study holds.

The comparison (E2), concordance (E6) and attribution (E3/E3') services decide what agreed and whether
bioAF's side is cleared. This module reads their verdicts onto the inventory's findings and decides
nothing scientific of its own:

- **Supported**: every required claim of the finding is supported by a valid, conclusive independent
  assessment: a reanalysis concordant with the authors' result set under the claim's own statistical
  definition, or a finding-tier metric within tolerance.
- **Discrepancy**: every required claim was conclusively assessed and none supported it, and each
  divergence is one the classifier could not attribute to bioAF's side.
- Otherwise the finding stays in the total, **inconclusive** (attempted, no conclusion), **blocked**
  (access, data not deposited, methods not recoverable, a bioAF limitation), **unresolved** (bioAF could
  not resolve the inputs or their interpretation) or **not attempted**, each with its reason.

Author-result consistency, technical QC, a reanalysis count and a model's opinion are supporting facts
shown beneath a finding. None of them moves a finding into the assessed numerator.

Only current evidence counts. An artifact stamped with an earlier selection revision is stale and is
ignored (``validation_revisions`` normally moves it to history first). Pure: no database, no model.
"""

from __future__ import annotations

from app.services.validation_scorecard import (
    BLOCKED,
    DISCREPANCY,
    INCONCLUSIVE,
    NOT_ATTEMPTED,
    SUPPORTED,
    TECHNICAL,
    UNRESOLVED,
)

# Why a finding has no conclusive result. Each keeps a scientific discrepancy distinct from an access
# restriction and from a limitation of bioAF (plan_8 section 3, blocker severity).
CAUSE_ACCESS = "access"
CAUSE_NOT_DEPOSITED = "not_deposited"
CAUSE_METHODS = "methods_unrecoverable"
CAUSE_BIOAF = "bioaf_limitation"
CAUSE_UNRESOLVED = "bioaf_unresolved"
CAUSE_PREREQUISITE = "prerequisite_failed"
CAUSE_LABELS = {
    CAUSE_ACCESS: "Access required",
    CAUSE_NOT_DEPOSITED: "Required data not deposited",
    CAUSE_METHODS: "Methods cannot be reconstructed",
    CAUSE_BIOAF: "bioAF limitation",
    CAUSE_UNRESOLVED: "Not resolved by bioAF",
    CAUSE_PREREQUISITE: "A prerequisite check failed",
}
CAUSE_EXPLANATIONS = {
    CAUSE_ACCESS: "Access required; no negative scientific conclusion.",
    CAUSE_NOT_DEPOSITED: "The data this needs are not publicly available; the authors may be able to provide them.",
    CAUSE_METHODS: "The paper, its code and its other evidence do not state the analysis decisions this needs.",
    CAUSE_BIOAF: "A limitation of bioAF, not a finding about the paper.",
    CAUSE_UNRESOLVED: "bioAF could not resolve the inputs or their interpretation; this is not a finding about the paper.",
    CAUSE_PREREQUISITE: "A check this assessment depends on did not pass, so the assessment is not valid.",
}
_CAUSE_ORDER = (CAUSE_ACCESS, CAUSE_NOT_DEPOSITED, CAUSE_METHODS, CAUSE_BIOAF, CAUSE_UNRESOLVED)
_BLOCKING_CAUSES = {CAUSE_ACCESS, CAUSE_NOT_DEPOSITED, CAUSE_METHODS, CAUSE_BIOAF}

# A concluded run's governing limitation, read onto the finding it was checking.
_LIMITATION_CAUSES = {
    "controlled_access": CAUSE_ACCESS,
    "access_refused": CAUSE_ACCESS,
    "missing_input": CAUSE_NOT_DEPOSITED,
    "unsupported_acquisition": CAUSE_BIOAF,
    "unsupported_processing": CAUSE_BIOAF,
    "reference_unavailable": CAUSE_BIOAF,
    "no_compatible_contrast": CAUSE_BIOAF,
    "resource_limit": CAUSE_BIOAF,
}

# A check's deciding requirement, read onto the finding it would have tested.
_REQUIREMENT_CAUSES = {
    "processed_matrix": CAUSE_NOT_DEPOSITED,
    "result_table": CAUSE_NOT_DEPOSITED,
    "measured_file": CAUSE_NOT_DEPOSITED,
    "workflow": CAUSE_BIOAF,
    "qc_binding": CAUSE_BIOAF,
    "not_applicable": CAUSE_BIOAF,
}
# Unresolved only for what the run itself establishes; such a claim could have been selected.
_PENDING_REQUIREMENTS = ("sample_mapping", "deposit_listing")

_TERMINAL = ("classified", "plan_declined", "error")

_CONCORDANCE_CRITERIA = (
    "Concordance with the authors' result set under the claim's statistical definition: supported when the "
    "overlap is enriched beyond chance (p <= 0.05) and at least half of the authors' findings are recovered in "
    "the same direction; a discrepancy when the overlap is no better than chance or none is recovered in the "
    "same direction."
)
_QC_CRITERIA = "The value bioAF computed is within the metric's tolerance of the value the paper states."


def _execution_checks(target: dict) -> tuple[str, ...]:
    """The independent checks that could assess this claim, by its type."""
    from app.services.validation_checks import SCALAR, claim_type

    return ("qc_metric",) if claim_type(target) == SCALAR else ("processed_reanalysis", "raw_reanalysis")


def _sentence(text: str | None) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


class _Evidence:
    """The study's current evidence, read once: stale artifacts dropped, comparison rows placed on claims."""

    def __init__(self, targets: list[dict], plan: dict, evidence: dict, study: dict):
        self.targets = targets
        self.plan = plan
        self.evidence = evidence
        self.state = study.get("state")
        selection = plan.get("analysis_selection") or {}
        self.current = selection.get("current") if isinstance(selection.get("current"), dict) else None
        self.refusal = selection.get("refusal") if isinstance(selection, dict) else None
        self.unassessed = {
            u.get("claim_index"): u.get("reason") for u in selection.get("unassessed") or [] if isinstance(u, dict)
        }
        revision = (self.current or {}).get("revision")
        self.revision = revision if isinstance(revision, int) else None
        self.level3 = self.artifact("level3") or {}
        self.level3_result = self.artifact("level3_result") or {}
        self.classification = self.artifact("classification_result") or {}
        self.rows = self._rows()

    def artifact(self, key: str):
        """The artifact, unless it was computed for an earlier selection revision than the current one."""
        stamp = (self.evidence.get("artifact_revisions") or {}).get(key)
        if isinstance(stamp, int) and self.revision is not None and stamp < self.revision:
            return None
        return self.evidence.get(key)

    def _rows(self) -> dict[int, dict]:
        """Each claim's QC comparison row. Rows follow the evidence's own copy of the targets, which
        carries each target's id; a copy written before ids is matched by the claim's own content."""
        comparisons = [c for c in self.classification.get("comparisons") or [] if isinstance(c, dict)]
        copies = [t for t in self.evidence.get("comparison_targets") or [] if isinstance(t, dict)]
        by_id = {t.get("id"): i for i, t in enumerate(self.targets) if t.get("id") is not None}
        rows: dict[int, dict] = {}
        for copy, row in zip(copies, comparisons):
            index = by_id.get(copy.get("id")) if copy.get("id") is not None else None
            if index is None:
                index = next(
                    (
                        i
                        for i, t in enumerate(self.targets)
                        if i not in rows
                        and t.get("claim_text") == copy.get("claim_text")
                        and t.get("metric_key") == copy.get("metric_key")
                        and t.get("claimed_value") == copy.get("claimed_value")
                    ),
                    None,
                )
            if index is not None:
                rows[index] = row
        return rows

    def level3_claim(self) -> int | None:
        index = self.level3.get("claim_index")
        if isinstance(index, int) and not isinstance(index, bool):
            return index
        # A bundle written before claims were selected: the claim on the contrast it tested, if one.
        name = self.level3.get("contrast")
        contrasts = ((self.plan.get("differential_design") or {}).get("contrasts")) or []
        on_contrast = [
            i
            for i, t in enumerate(self.targets)
            if isinstance(t.get("contrast_index"), int)
            and 0 <= t["contrast_index"] < len(contrasts)
            and (contrasts[t["contrast_index"]] or {}).get("name") == name
        ]
        return on_contrast[0] if name and len(on_contrast) == 1 else None

    def selected(self, claims: list[int]) -> bool:
        return self.current is not None and self.current.get("claim_index") in claims


def _result(status, *, method, reason, evidence, criteria, claim_index) -> dict:
    return {
        "claim_index": claim_index,
        "status": status,
        "method": method,
        "reason": reason,
        "evidence": evidence,
        "criteria": criteria,
    }


def _definition_applied(level3: dict, plan: dict) -> bool:
    """Whether the reanalysis applied the claim's own statistical definition (the classifier's
    ``thresholds_matched``)."""
    if isinstance((level3.get("cutoffs") or {}).get("significance"), dict):
        return True
    params = level3.get("parameters") or {}
    if params.get("lfc_threshold") is not None and params.get("padj_threshold") is not None:
        return True
    legacy = (plan.get("differential_design") or {}).get("thresholds") or {}
    return legacy.get("log2fc") is not None and legacy.get("padj") is not None


def _concordance_words(concordance: dict) -> str:
    return (
        f"{concordance.get('concordant', 0)} of {concordance.get('paper_n', 0)} of the authors' findings recovered in "
        f"the same direction (overlap enrichment p = {float(concordance.get('enrichment_p', 1.0)):.1e})"
    )


def _level3_result(index: int, ev: _Evidence) -> dict | None:
    if not ev.level3_result or ev.level3_claim() != index:
        return None
    method = "processed_reanalysis" if ev.level3.get("source") == "deposit" else "raw_reanalysis"
    stamp = (ev.evidence.get("artifact_revisions") or {}).get("level3_result")
    evidence = [f"level3_result (selection revision {stamp})" if isinstance(stamp, int) else "level3_result"]

    def result(status, reason):
        return _result(
            status, method=method, reason=reason, evidence=evidence, criteria=_CONCORDANCE_CRITERIA, claim_index=index
        )

    if not _definition_applied(ev.level3, ev.plan):
        return result(
            INCONCLUSIVE,
            "The reanalysis did not apply the claim's statistical definition, so it is not a valid comparison.",
        )
    concordance = ev.level3_result.get("concordance")
    if not isinstance(concordance, dict):
        return result(
            INCONCLUSIVE,
            "The reanalysis ran, and no authors' result set was identified to compare it with; its count carries no "
            "verdict of its own.",
        )
    verdict = concordance.get("verdict")
    if verdict == "agree":
        return result(SUPPORTED, _sentence(_concordance_words(concordance)))
    if verdict == "partial":
        return result(
            INCONCLUSIVE,
            f"Part of the authors' result set was recovered ({_concordance_words(concordance)}), below the agreement "
            "criterion.",
        )
    if verdict == "diverge":
        attribution = ev.classification.get("attribution") or {}
        if attribution.get("our_side") == "cleared":
            return result(DISCREPANCY, _sentence(_concordance_words(concordance)))
        if not ev.classification:
            return result(
                INCONCLUSIVE,
                "The reanalysis diverged, and whether bioAF's side explains it has not been assessed yet.",
            )
        reasons = "; ".join(attribution.get("reasons") or []) or "the reason was not recorded"
        return result(INCONCLUSIVE, f"The reanalysis diverged, and bioAF's side could explain it: {reasons}.")
    notes = "; ".join(concordance.get("notes") or []) or "the reason was not recorded"
    return result(INCONCLUSIVE, f"The concordance could not be computed: {notes}.")


def _qc_result(index: int, ev: _Evidence, *, technical: bool) -> tuple[dict | None, list[dict]]:
    """The claim's QC comparison as a result, and any technical QC fact to show beneath it."""
    from app.services.validation_classifier_service import _tier

    row = ev.rows.get(index)
    if row is None:
        return None, []
    verdict, mapped = row.get("verdict"), row.get("mapped_key")
    evidence = [f"classification_result comparison ({row.get('metric_key') or mapped})"]

    def result(status, reason):
        return _result(
            status, method="qc_metric", reason=reason, evidence=evidence, criteria=_QC_CRITERIA, claim_index=index
        )

    if verdict in ("agree", "diverge") and row.get("advisory"):
        return result(INCONCLUSIVE, _sentence(f"Compared as advisory evidence only: {row.get('advisory_reason')}")), []
    if verdict == "not_compared":
        return result(INCONCLUSIVE, _sentence(row.get("advisory_reason") or "the claim was not compared")), []
    if verdict == "not_computed" and mapped:
        return result(INCONCLUSIVE, f"The run computed no value for {mapped}."), []
    if verdict not in ("agree", "diverge"):
        return None, []
    if _tier(mapped) != "finding" and not technical:
        # Technical QC clearance is a supporting fact, never a finding-level comparison.
        words = "agrees with" if verdict == "agree" else "differs from"
        return None, [{"kind": "technical_qc", "text": f"Technical QC {words} the paper ({mapped})"}]
    if verdict == "agree":
        return result(SUPPORTED, f"{mapped} is within tolerance of the paper's value."), []
    explained = (ev.classification.get("divergence_attribution") or {}).get(mapped)
    if explained:
        return result(
            INCONCLUSIVE, _sentence(explained.get("explanation") or "a known tool difference explains it")
        ), []
    if technical:
        return result(DISCREPANCY, f"{mapped} is outside tolerance of the paper's value."), []
    attribution = ev.classification.get("attribution") or {}
    if attribution.get("our_side") == "cleared":
        return result(DISCREPANCY, f"{mapped} is outside tolerance of the paper's value."), []
    reasons = "; ".join(attribution.get("reasons") or []) or "the reason was not recorded"
    return result(INCONCLUSIVE, f"{mapped} diverged, and bioAF's side could explain it: {reasons}."), []


def _supporting(index: int, ev: _Evidence, consistency: dict) -> list[dict]:
    """Facts shown beneath the finding that earn it nothing: the authors' own table, the reanalysis count."""
    rows: list[dict] = []
    record = consistency.get(index)
    if isinstance(record, dict) and record.get("label"):
        table = f" ({record['table']})" if record.get("table") else ""
        rows.append({"kind": "author_results", "text": f"{record['label']}{table}"})
    count = (ev.level3_result.get("claim_count") or {}) if ev.level3_claim() == index else {}
    if count.get("words"):
        rows.append({"kind": "reanalysis_count", "text": f"{count.get('label')}: {count['words']}"})
    return rows


def _cause_of_check(check: dict) -> str:
    requirement = check.get("requirement")
    reason = str(check.get("reason") or "")
    if requirement == "raw_reads":
        if "controlled access" in reason:
            return CAUSE_ACCESS
        if "no adapter" in reason:
            return CAUSE_BIOAF
        return CAUSE_NOT_DEPOSITED
    if requirement == "reference":
        return CAUSE_BIOAF if check.get("status") == "unavailable" else CAUSE_UNRESOLVED
    if requirement == "predicate":
        return CAUSE_METHODS if check.get("status") == "unavailable" else CAUSE_UNRESOLVED
    return _REQUIREMENT_CAUSES.get(requirement, CAUSE_UNRESOLVED)


def _unassessed(status: str, cause: str | None, detail: str | None) -> dict:
    explanation = CAUSE_EXPLANATIONS.get(cause) if cause else None
    reason = " ".join(part for part in (explanation, _sentence(detail)) if part) or None
    return {
        "status": status,
        "cause": cause,
        "cause_label": CAUSE_LABELS.get(cause) if cause else None,
        "reason": reason,
    }


def _from_checks(claims: list[int], ev: _Evidence) -> dict:
    """Why an unselected finding was not assessed, from its claims' own checks."""
    from app.services.validation_report_summary import CLAIM_CHECK_LABELS

    found: list[tuple[str, dict]] = []
    for index in claims:
        target = ev.targets[index] if 0 <= index < len(ev.targets) else {}
        checks = target.get("checks") or {}
        for key in _execution_checks(target):
            if isinstance(checks.get(key), dict):
                found.append((key, checks[key]))
    if not found:
        return _unassessed(NOT_ATTEMPTED, None, "Not attempted")
    if any(
        c.get("status") == "available"
        or (c.get("status") == "unresolved" and c.get("requirement") in _PENDING_REQUIREMENTS)
        for _key, c in found
    ):
        if ev.refusal and not ev.current:
            return _unassessed(
                NOT_ATTEMPTED, None, f"no claim could be checked on the requested route: {ev.refusal.get('reason')}"
            )
        reason = next((ev.unassessed.get(i) for i in claims if ev.unassessed.get(i)), None)
        return _unassessed(NOT_ATTEMPTED, None, reason or "not selected for this run")
    detail = "; ".join(f"{CLAIM_CHECK_LABELS.get(key, key)}: {c.get('reason') or c.get('status')}" for key, c in found)
    unresolved = [c for _key, c in found if c.get("status") == "unresolved"]
    if unresolved:
        return _unassessed(UNRESOLVED, _cause_of_check(unresolved[0]), detail)
    causes = {_cause_of_check(c) for _key, c in found}
    cause = next(c for c in _CAUSE_ORDER if c in causes)
    return _unassessed(BLOCKED if cause in _BLOCKING_CAUSES else UNRESOLVED, cause, detail)


def _why_not(claims: list[int], ev: _Evidence, *, technical_only: list[str]) -> dict:
    """Why a finding has no conclusive result, when nothing attempted it."""
    if ev.state == "plan_declined":
        return _unassessed(NOT_ATTEMPTED, None, "the plan was declined, so nothing was run")
    selected = ev.selected(claims)
    if selected or ev.current is None and ev.state == "error":
        if ev.state == "error":
            return _unassessed(
                UNRESOLVED, CAUSE_UNRESOLVED, "bioAF hit an error while running the study; it can be retried"
            )
        if ev.state not in _TERMINAL:
            return _unassessed(NOT_ATTEMPTED, None, "not run yet; the study is in progress")
        from app.services.validation_report_summary import LIMITATION_LABELS

        governing = [
            lim for lim in (ev.evidence.get("completion") or {}).get("limitations") or [] if isinstance(lim, dict)
        ]
        if governing:
            limitation = governing[0]
            cause = _LIMITATION_CAUSES.get(limitation.get("kind"), CAUSE_UNRESOLVED)
            label = LIMITATION_LABELS.get(limitation.get("kind"), limitation.get("kind"))
            detail = limitation.get("detail") or limitation.get("resource")
            words = f"{label}: {detail}" if detail else label
            return _unassessed(BLOCKED if cause in _BLOCKING_CAUSES else UNRESOLVED, cause, words)
        if isinstance(ev.evidence.get("level3_failed"), dict):
            return _unassessed(
                UNRESOLVED, CAUSE_UNRESOLVED, f"the reanalysis failed: {ev.evidence['level3_failed'].get('reason')}"
            )
        if isinstance(ev.evidence.get("level3_skipped"), dict):
            return _unassessed(
                UNRESOLVED,
                CAUSE_UNRESOLVED,
                f"the reanalysis was not run: {ev.evidence['level3_skipped'].get('reason')}",
            )
        if technical_only:
            return _unassessed(
                NOT_ATTEMPTED, None, "only technical QC was compared for it; no finding-level comparison was made"
            )
        return _unassessed(NOT_ATTEMPTED, None, "the run produced no result for it")
    return _from_checks(claims, ev)


def _combine(
    finding: dict, results: list[dict | None], claims: list[int], ev: _Evidence, supporting: list[dict]
) -> dict:
    """rubric version 1's finding-level rule over the required claims' results."""
    attempted = [r for r in results if r is not None]
    conclusive = [r for r in attempted if r["status"] in (SUPPORTED, DISCREPANCY)]
    methods = list(dict.fromkeys(r["method"] for r in attempted))
    base = {
        "assessment_method": methods[0] if len(methods) == 1 else None,
        "supporting_evidence_ids": [e for r in attempted for e in r["evidence"]],
        "subchecks": [
            {
                "claim_index": c,
                "status": (r or {}).get("status"),
                "method": (r or {}).get("method"),
                "reason": (r or {}).get("reason"),
            }
            for c, r in zip(claims, results)
        ],
        "cause": None,
        "cause_label": None,
    }
    if claims and len(attempted) == len(claims) and all(r["status"] == SUPPORTED for r in attempted):
        return {**base, "status": SUPPORTED, "reason": " ".join(r["reason"] for r in attempted if r["reason"]) or None}
    if claims and len(attempted) == len(claims) and all(r["status"] == DISCREPANCY for r in attempted):
        return {
            **base,
            "status": DISCREPANCY,
            "reason": " ".join(r["reason"] for r in attempted if r["reason"]) or None,
        }
    if attempted:
        if len(claims) == 1:
            return {**base, "status": INCONCLUSIVE, "reason": attempted[0]["reason"]}
        mixed = {r["status"] for r in conclusive} == {SUPPORTED, DISCREPANCY}
        head = f"{len(conclusive)} of {len(claims)} required claims were conclusively assessed"
        if mixed:
            head += ", and they disagree"
        details = " ".join(r["reason"] for r in attempted if r["reason"])
        return {**base, "status": INCONCLUSIVE, "reason": f"{head}. {details}".strip()}
    technical_only = [c["text"] for c in supporting if c["kind"] == "technical_qc"]
    return {**base, **_why_not(claims, ev, technical_only=technical_only)}


def finding_outcomes(
    inventory: dict,
    *,
    targets: list[dict],
    plan: dict,
    evidence: dict,
    study: dict,
    consistency: dict[int, dict | None] | None = None,
) -> dict[str, dict]:
    """One outcome record per finding in ``inventory``, keyed by finding id.

    ``targets`` are the plan's claims in id order (a claim's position is its index); ``consistency``
    is each claim's check against the authors' own results, as the report projects it.
    """
    from app.services.validation_report_summary import CLAIM_CHECK_LABELS

    ev = _Evidence([t for t in targets or [] if isinstance(t, dict)], plan or {}, evidence or {}, study or {})
    consistency = consistency or {}
    outcomes: dict[str, dict] = {}
    findings = [f for f in inventory.get("findings") or [] if isinstance(f, dict)]
    for finding in findings:
        technical = ((finding.get("importance") or {}).get("category")) == TECHNICAL
        claims = [c for c in finding.get("required") or finding.get("claim_indices") or [] if isinstance(c, int)]
        results: list[dict | None] = []
        supporting: list[dict] = []
        for index in claims:
            result = _level3_result(index, ev)
            if result is None:
                result, facts = _qc_result(index, ev, technical=technical)
                supporting.extend(facts)
            supporting.extend(_supporting(index, ev, consistency))
            results.append(result)
        # The same fact about two claims of one finding (both counts consistent with one table) is one line.
        supporting = [dict(pair) for pair in dict.fromkeys(tuple(sorted(s.items())) for s in supporting)]
        outcome = _combine(finding, results, claims, ev, supporting)
        method = outcome.get("assessment_method")
        outcomes[finding["id"]] = {
            "finding_id": finding["id"],
            "inventory_revision": inventory.get("revision"),
            "analysis_selection_revision": ev.revision,
            **outcome,
            "method_label": CLAIM_CHECK_LABELS.get(method) if method else None,
            "supporting_checks": supporting,
            "comparison_criteria": {
                "rule": (finding.get("criteria") or {}).get("rule"),
                "words": (finding.get("criteria") or {}).get("words"),
                "subchecks": list(dict.fromkeys(r["criteria"] for r in results if r is not None)),
            },
        }

    # A technical prerequisite that did not pass invalidates what depends on it: the dependent
    # assessment becomes unresolved rather than counting either way.
    for finding in findings:
        if outcomes[finding["id"]]["status"] != DISCREPANCY or not finding.get("prerequisite_for"):
            continue
        if ((finding.get("importance") or {}).get("category")) != TECHNICAL:
            continue
        for dependent in finding["prerequisite_for"]:
            outcome = outcomes.get(dependent)
            if outcome is None or outcome["status"] not in (SUPPORTED, DISCREPANCY):
                continue
            detail = (
                f"{finding.get('description') or finding['id']} did not pass. {outcome.get('reason') or ''}".strip()
            )
            outcome.update(_unassessed(UNRESOLVED, CAUSE_PREREQUISITE, detail))
    return outcomes
