"""plan_8_1 stage 4: weighted rubric version 2, the evidence a finding's status rests on.

Under version 1 only a conclusive independent assessment scored, so the preliminary evidence a report
held (the authors' own deposited result tables above all) could never earn a score. Version 2 keeps the
finding as the unit, the weights 2, 1 and 0, the two metrics and their invariants, and adds one kind of
evidence to the numbers:

- **Independent assessment**, as in version 1: a reanalysis concordant with the authors' result set under
  the claim's own definition, or a finding-tier metric within tolerance.
- **Author-result consistency**: the claim checked against the authors' own table at its predicate. It
  shows the paper's text agrees with its own results; it is never an independent assessment, and the
  depth says so wherever the score appears.

**Authority, declared before any result** (``AUTHORITY``, recorded with the inventory):

1. A valid, conclusive independent assessment governs. Comparable ones that conflict leave the claim
   inconclusive: "independent assessments disagree; not reconciled".
2. Otherwise the authors' sources govern at consistency depth. A source a documented correction supersedes
   stays as history; comparable authoritative sources that conflict leave the claim inconclusive, "author-
   result sources disagree; not reconciled", with no assessed weight. Neither a score nor the order sources
   arrive in decides authority.
3. Otherwise the claim is unassessed, with its reason.

Every outcome is kept. A lower-authority outcome that disagrees with the governing one is a concern beneath
the claim (and in the messages when the finding is primary); a conflict among the authors' sources stays
visible even when an independent assessment governs.

**Resource statements** (``resource_statements``) are verified against the resources they describe and
shown beneath the findings those resources serve. They never enter either metric; absence never fails a
finding; a retrieval that failed is "not established", never "contradicted"; access is context.

Pure: no database, no model.
"""

from __future__ import annotations

from app.services.validation_scorecard import (
    CONSISTENCY_DEPTH as CONSISTENCY,
)
from app.services.validation_scorecard import (
    DEPTH_LABELS,
    DISCREPANCY,
    INCONCLUSIVE,
    SUPPORTED,
)
from app.services.validation_scorecard import (
    INDEPENDENT_DEPTH as INDEPENDENT,
)

# plan_8_1 labels, pending the owner's sign-off.
INDEPENDENT_CONFLICT = "independent assessments disagree; not reconciled"
SOURCE_CONFLICT = "author-result sources disagree; not reconciled"
TEXT_DISAGREES = "the paper's text disagrees with its own table"
TABLE_AGREES = "the authors' table agrees with the paper, and the independent assessment did not"

# 4.3: declared before results, and recorded with every inventory established under version 2.
AUTHORITY = {
    "order": [INDEPENDENT, CONSISTENCY],
    "required_subchecks": "every claim of a finding",
    "author_sources": (
        "a source's experiment, contrast, statistical definition and documented version or correction status "
        "decide its authority; a source a documented correction supersedes stays as history; comparable "
        "authoritative sources that conflict are inconclusive until reconciled; neither a filename nor "
        "agreement with the paper overrides an eligible source"
    ),
}

_CONSISTENCY_STATUS = {"agree": SUPPORTED, "disagree": DISCREPANCY}


def _conclusive(results: list[dict]) -> list[dict]:
    return [r for r in results if r and r.get("status") in (SUPPORTED, DISCREPANCY)]


def _source_view(source: dict) -> dict:
    return {
        "table": source.get("table"),
        "source": source.get("source"),
        "outcome": source.get("outcome"),
        "version_status": source.get("version_status"),
        "reason": source.get("reason"),
    }


def _source_words(source: dict) -> str:
    words = source.get("reason") or source.get("label") or source.get("outcome") or ""
    table = source.get("table")
    return f"{words} ({table})" if table and table not in str(words) else str(words)


def govern_claim(index: int, independent: list[dict | None], sources: list[dict | None]) -> dict | None:
    """The claim's status under the declared authority, with its depth, its concerns and the authority
    decision; None when nothing assessed it conclusively and nothing conflicts."""
    attempts: list[dict] = [r for r in independent or [] if r]
    found: list[dict] = [s for s in sources or [] if isinstance(s, dict)]
    superseded = [s for s in found if s.get("version_status") == "superseded"]
    corrected = [s for s in found if s.get("version_status") == "corrected"]
    eligible = [s for s in found if s.get("version_status") != "superseded" or not corrected]
    conclusive_sources = [s for s in eligible if s.get("outcome") in _CONSISTENCY_STATUS]
    source_statuses = {_CONSISTENCY_STATUS[s["outcome"]] for s in conclusive_sources}
    authority = {
        "sources": [_source_view(s) for s in found],
        "governing": [s.get("table") for s in conclusive_sources] if len(source_statuses) == 1 else [],
        "excluded": [
            {"table": s.get("table"), "why": "superseded by a documented correction"} for s in superseded if corrected
        ],
    }
    concerns: list[dict] = []
    if len(source_statuses) > 1:
        concerns.append({"kind": "source_conflict", "text": SOURCE_CONFLICT})

    def _claim(status, *, depth, method, reason, evidence):
        return {
            "claim_index": index,
            "status": status,
            "depth": depth,
            "method": method,
            "reason": reason,
            "evidence": evidence,
            "concerns": concerns,
            "authority": authority,
            # Every outcome is kept: the independent attempts beside the author-result sources.
            "independent": [
                {"status": r.get("status"), "method": r.get("method"), "reason": r.get("reason")} for r in attempts
            ],
        }

    decided = _conclusive(attempts)
    if decided:
        statuses = {r["status"] for r in decided}
        if len(statuses) > 1:
            return _claim(
                INCONCLUSIVE,
                depth=None,
                method=None,
                reason=INDEPENDENT_CONFLICT,
                evidence=[e for r in decided for e in r.get("evidence") or []],
            )
        governing = decided[0]
        if len(source_statuses) == 1:
            table_status = next(iter(source_statuses))
            if governing["status"] == SUPPORTED and table_status == DISCREPANCY:
                concerns.append({"kind": "text_disagrees", "text": TEXT_DISAGREES})
            elif governing["status"] == DISCREPANCY and table_status == SUPPORTED:
                concerns.append({"kind": "table_agrees", "text": TABLE_AGREES})
        return _claim(
            governing["status"],
            depth=INDEPENDENT,
            method=governing.get("method"),
            reason=governing.get("reason"),
            evidence=list(governing.get("evidence") or []),
        )
    if len(source_statuses) > 1:
        return _claim(INCONCLUSIVE, depth=None, method="author_results", reason=SOURCE_CONFLICT, evidence=[])
    if conclusive_sources:
        status = next(iter(source_statuses))
        reason = "; ".join(_source_words(s) for s in conclusive_sources) or None
        return _claim(
            status,
            depth=CONSISTENCY,
            method="author_results",
            reason=reason,
            evidence=[f"author_results ({s.get('table')})" for s in conclusive_sources],
        )
    if attempts:
        # Attempted independently and inconclusive: that is the claim's result, as in version 1.
        attempt = attempts[0]
        return _claim(
            attempt["status"],
            depth=None,
            method=attempt.get("method"),
            reason=attempt.get("reason"),
            evidence=list(attempt.get("evidence") or []),
        )
    return None


def combine_finding(finding: dict, governed: list[dict | None], claims: list[int]) -> dict:
    """plan_8's all-required-claims rule over the governed claims, with the finding's depth: the
    shallowest governing depth among its required claims."""
    attempted = [g for g in governed if g is not None]
    conclusive = _conclusive(attempted)
    concerns = [c for g in attempted for c in g.get("concerns") or []]
    concerns = [dict(pair) for pair in dict.fromkeys(tuple(sorted(c.items())) for c in concerns)]
    base: dict = {
        "subchecks": [
            {
                "claim_index": c,
                "status": (g or {}).get("status"),
                "method": (g or {}).get("method"),
                "depth": (g or {}).get("depth"),
                "reason": (g or {}).get("reason"),
                "authority": (g or {}).get("authority"),
            }
            for c, g in zip(claims, governed)
        ],
        "concerns": concerns,
        "supporting_evidence_ids": [e for g in attempted for e in g.get("evidence") or []],
        "cause": None,
        "cause_label": None,
    }
    all_conclusive = bool(claims) and len(conclusive) == len(claims)
    depth = None
    if all_conclusive:
        depth = INDEPENDENT if all(g["depth"] == INDEPENDENT for g in conclusive) else CONSISTENCY
    methods = list(dict.fromkeys(g["method"] for g in attempted if g.get("method")))
    base["depth"] = depth
    base["depth_label"] = DEPTH_LABELS.get(depth) if depth else None
    base["governing"] = {
        "method": methods[0] if len(methods) == 1 else None,
        "evidence": base["supporting_evidence_ids"],
    }
    base["assessment_method"] = methods[0] if len(methods) == 1 else None
    reasons = " ".join(g["reason"] for g in attempted if g.get("reason"))
    if all_conclusive and all(g["status"] == SUPPORTED for g in conclusive):
        return {**base, "status": SUPPORTED, "reason": reasons or None}
    if all_conclusive and all(g["status"] == DISCREPANCY for g in conclusive):
        return {**base, "status": DISCREPANCY, "reason": reasons or None}
    if attempted:
        if len(claims) == 1:
            return {**base, "status": INCONCLUSIVE, "reason": attempted[0].get("reason")}
        mixed = {g["status"] for g in conclusive} == {SUPPORTED, DISCREPANCY}
        head = f"{len(conclusive)} of {len(claims)} required claims were conclusively assessed"
        if mixed:
            head += ", and they disagree"
        return {**base, "status": INCONCLUSIVE, "reason": f"{head}. {reasons}".strip()}
    return {**base, "status": None, "reason": None}


# ---- 4.4: resource statements, verified and shown, never scored ---------------------------------------

VERIFIED = "verified"
CONTRADICTED = "contradicted"
NOT_ESTABLISHED = "not_established"
STATEMENT_LABELS = {VERIFIED: "Verified", CONTRADICTED: "Contradicted", NOT_ESTABLISHED: "Not established"}


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def _check(field: str, outcome: str, detail: str) -> dict:
    return {"field": field, "outcome": outcome, "outcome_label": STATEMENT_LABELS[outcome], "detail": detail}


def _statement_checks(
    deposit: dict, experiment: dict, *, organism: str | None, species: dict | None, sample_count
) -> list[dict]:
    """Each explicit statement the paper makes about this deposit, against the deposit's own record. A
    lookup that failed establishes nothing; access restricts the bytes, not the public record."""
    from app.services.pipeline_mapper import library_strategy_conflict

    exists = deposit.get("exists")
    if exists == "no":
        return [_check("registered", CONTRADICTED, "the registry holds no record under the identifier the paper gives")]
    if exists != "yes":
        reason = deposit.get("failure_reason") or "the registry could not be reached"
        return [_check("registered", NOT_ESTABLISHED, f"{reason}; nothing about this resource is established")]
    checks = [_check("registered", VERIFIED, "the registry holds the record the paper names")]
    organisms = [o for o in deposit.get("organisms") or [] if o]
    if organism and organisms:
        agree = _norm(organism) in {_norm(o) for o in organisms}
        checks.append(
            _check(
                "organism",
                VERIFIED if agree else CONTRADICTED,
                f"the paper states {organism}; the record declares {', '.join(organisms)}",
            )
        )
    elif organism and species and species.get("verdict") in ("ok", "mismatch"):
        checks.append(
            _check(
                "organism", VERIFIED if species["verdict"] == "ok" else CONTRADICTED, str(species.get("detail") or "")
            )
        )
    strategies = [s for s in (deposit.get("listing") or {}).get("library_strategies") or [] if s]
    workflow = experiment.get("workflow")
    # Only an assay the paper states plainly, mapped to a workflow, is compared with what the record registers.
    if workflow and strategies and experiment.get("assay") and not experiment.get("assay_ambiguous"):
        compatible = [s for s in strategies if not library_strategy_conflict(workflow, s)]
        checks.append(
            _check(
                "assay",
                VERIFIED if compatible else CONTRADICTED,
                f"the paper describes {experiment.get('assay') or workflow}; the record registers {', '.join(strategies)}",
            )
        )
    registered = deposit.get("registered_samples")
    if isinstance(sample_count, int) and sample_count > 0 and isinstance(registered, int) and registered > 0:
        if registered == sample_count:
            checks.append(
                _check(
                    "sample_count",
                    VERIFIED,
                    f"the paper states {sample_count} samples; the record registers {registered}",
                )
            )
        else:
            # The paper's count can span several deposits, so a different count is not a contradiction.
            checks.append(
                _check(
                    "sample_count",
                    NOT_ESTABLISHED,
                    f"the paper states {sample_count} samples and the record registers {registered}; whether the "
                    "paper's count covers only this deposit is not established",
                )
            )
    return checks


def resource_statements(plan: dict, evidence: dict, *, source_accession: str | None = None) -> list[dict]:
    """What the paper states about each resource it names, checked against the resource's own record, with
    the reported experiments the resource serves. Shown beneath those findings; never in either metric."""
    deposits = {
        str(d.get("accession") or "").upper(): d
        for d in ((evidence.get("capabilities") or {}).get("deposits") or [])
        if isinstance(d, dict) and d.get("accession")
    }
    sheet = plan.get("sample_sheet") if isinstance(plan.get("sample_sheet"), dict) else {}
    species = (evidence.get("precompute_checks") or {}).get("species_matches")
    experiments = {e.get("id"): e for e in plan.get("reported_experiments") or [] if isinstance(e, dict)}
    served: dict[str, list] = {}
    for experiment in experiments.values():
        for identifier in experiment.get("resources") or []:
            served.setdefault(str(identifier or "").upper(), []).append(experiment.get("id"))
    for resource in plan.get("resources") or []:
        if isinstance(resource, dict) and resource.get("identifier"):
            ids = served.setdefault(str(resource["identifier"]).upper(), [])
            ids.extend(e for e in resource.get("reported_experiment_ids") or [] if e not in ids)
    rows = []
    for key, experiment_ids in served.items():
        deposit = deposits.get(key)
        if deposit is None:
            continue
        experiment = experiments.get(experiment_ids[0]) if experiment_ids else None
        checks = _statement_checks(
            deposit,
            experiment or {},
            organism=(experiment or {}).get("organism") or sheet.get("organism"),
            species=species if key == str(source_accession or "").upper() else None,
            sample_count=sheet.get("sample_count"),
        )
        outcomes = {c["outcome"] for c in checks}
        outcome = CONTRADICTED if CONTRADICTED in outcomes else (VERIFIED if VERIFIED in outcomes else NOT_ESTABLISHED)
        rows.append(
            {
                "identifier": deposit.get("accession"),
                "archive": deposit.get("archive"),
                "access": deposit.get("access"),
                "experiment_ids": list(dict.fromkeys(experiment_ids)),
                "checks": checks,
                "outcome": outcome,
                "outcome_label": STATEMENT_LABELS[outcome],
            }
        )
    return rows
