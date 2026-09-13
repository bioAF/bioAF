"""plan_8 sections 3 and 4: each finding's outcome, normalized from the evidence the study already holds.

The comparison and concordance services decide what agreed. This only reads their verdicts onto the
findings, under the eligibility table: a valid, conclusive independent assessment is supported or a
discrepancy; everything else stays in the total and says why it has no conclusive result. Author-result
consistency and technical QC are shown beneath a finding and never earn it credit.
"""

import copy

import pytest

from app.services.validation_finding_outcomes import finding_outcomes
from app.services.validation_scorecard import (
    BLOCKED,
    DISCREPANCY,
    INCONCLUSIVE,
    NOT_ATTEMPTED,
    SUPPORTED,
    UNRESOLVED,
)

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]


def _check(status, reason=None, requirement=None):
    return {"status": status, "reason": reason, "requirement": requirement}


_DE_AVAILABLE = {
    "qc_metric": _check("unavailable", "no QC metric measures a differential result", "qc_binding"),
    "author_results": _check("available", "the authors published t.txt.gz"),
    "processed_reanalysis": _check(
        "unresolved", "the sample mapping is established when the input is chosen", "sample_mapping"
    ),
    "raw_reanalysis": _check("available"),
}
_DE_CONTROLLED = {
    "qc_metric": _check("unavailable", "no QC metric measures a differential result", "qc_binding"),
    "author_results": _check("unavailable", "no result table is published for this claim's experiment", "result_table"),
    "processed_reanalysis": _check(
        "unavailable", "no processed matrix holding both arms is published", "processed_matrix"
    ),
    "raw_reanalysis": _check(
        "unavailable", "EGAS1 holds the reads, and bioAF cannot acquire them (controlled access)", "raw_reads"
    ),
}
_SCALAR = {
    "qc_metric": _check("available"),
    "author_results": _check("unavailable", "no deposited or attached file measures this quantity", "measured_file"),
    "processed_reanalysis": _check("unavailable", "bioAF computes no QC metric from processed files", "not_applicable"),
    "raw_reanalysis": _check("available", "the same run as the QC metric comparison; not a separate check (available)"),
}


def _target(text, *, contrast=0, checks=None, **extra):
    target = {"claim_text": text, "claimed_value": 100, "contrast_index": contrast, "cutoffs": _P, "checks": checks}
    target.update(extra)
    return target


def _finding(fid, category, claims, **extra):
    finding = {
        "id": fid,
        "description": f"finding {fid}",
        "claim_indices": claims,
        "required": claims,
        "prerequisite_for": [],
        "importance": {
            "category": category,
            "weight": {"primary": 2, "supporting": 1, "technical": 0}[category],
            "status": "validated",
        },
        "criteria": {"rule": "all_required_subchecks", "words": "w"},
    }
    finding.update(extra)
    return finding


def _inventory(*findings, revision=1):
    return {"rubric_version": 1, "revision": revision, "status": "established", "findings": list(findings)}


def _plan(*, claim=0, revision=2, contrasts=1):
    return {
        "differential_design": {"contrasts": [{"name": f"c{i}", "cutoffs": _P} for i in range(contrasts)]},
        "analysis_selection": {
            "current": {
                "revision": revision,
                "claim_index": claim,
                "check": "processed_reanalysis",
                "contrast_index": 0,
            },
            "unassessed": [{"claim_index": 1, "reason": "not selected for this run; its checks are available"}],
        },
    }


def _concordance(verdict, **extra):
    record = {
        "kind": "gene",
        "verdict": verdict,
        "paper_n": 100,
        "our_n": 90,
        "overlap": 70,
        "concordant": 65,
        "directional_overlap_frac": 0.65,
        "enrichment_p": 1e-30,
        "notes": [],
    }
    record.update(extra)
    return record


def _evidence(verdict="agree", *, claim=0, source="deposit", applied=True, attribution=None, stamp=2, **extra):
    level3 = {"claim_index": claim, "source": source, "contrast": "c0", "predicate": {"direction": "up"}}
    if applied:
        level3["cutoffs"] = {"significance": {"kind": "pvalue", "operator": "<", "value": 0.01}}
    evidence = {
        "level3": level3,
        "level3_result": {"concordance": _concordance(verdict) if verdict else None},
        "artifact_revisions": {"level3": stamp, "level3_result": stamp},
    }
    if attribution is not None:
        evidence["classification_result"] = {
            "comparisons": [],
            "attribution": attribution,
            "divergence_attribution": {},
        }
    evidence.update(extra)
    return evidence


_CLASSIFIED = {"state": "classified"}


def _outcomes(inventory, targets, plan, evidence, study=_CLASSIFIED, consistency=None):
    return finding_outcomes(
        inventory, targets=targets, plan=plan, evidence=evidence, study=study, consistency=consistency or {}
    )


_TWO = [_target("genes up", checks=_DE_AVAILABLE), _target("genes down", checks=_DE_AVAILABLE)]
_INVENTORY = _inventory(_finding("F1", "primary", [0]), _finding("F2", "supporting", [1]))


class TestAReanalysisAgainstTheAuthorsResults:
    def test_agreement_supports_the_finding_it_tested(self):
        outcomes = _outcomes(_INVENTORY, _TWO, _plan(), _evidence("agree"))
        f1 = outcomes["F1"]
        assert f1["status"] == SUPPORTED
        assert f1["assessment_method"] == "processed_reanalysis"
        assert f1["method_label"] == "Reanalysis of processed data"
        assert f1["analysis_selection_revision"] == 2
        assert f1["inventory_revision"] == 1
        assert "level3_result" in " ".join(f1["supporting_evidence_ids"])
        assert f1["comparison_criteria"]["subchecks"]
        assert outcomes["F2"]["status"] == NOT_ATTEMPTED

    def test_a_raw_reads_reanalysis_is_named_as_one(self):
        outcomes = _outcomes(_INVENTORY, _TWO, _plan(), _evidence("agree", source="pipeline"))
        assert outcomes["F1"]["assessment_method"] == "raw_reanalysis"

    def test_a_divergence_the_classifier_cleared_is_a_discrepancy(self):
        evidence = _evidence("diverge", attribution={"our_side": "cleared", "reasons": ["cleared"]})
        assert _outcomes(_INVENTORY, _TWO, _plan(), evidence)["F1"]["status"] == DISCREPANCY

    def test_a_divergence_our_side_could_explain_is_inconclusive(self):
        attribution = {
            "our_side": "suspected",
            "reasons": ["the differential method was not established as comparable"],
        }
        f1 = _outcomes(_INVENTORY, _TWO, _plan(), _evidence("diverge", attribution=attribution))["F1"]
        assert f1["status"] == INCONCLUSIVE
        assert "differential method" in f1["reason"]

    def test_a_divergence_not_yet_attributed_is_not_a_discrepancy(self):
        assert _outcomes(_INVENTORY, _TWO, _plan(), _evidence("diverge"))["F1"]["status"] == INCONCLUSIVE

    def test_a_partial_recovery_is_inconclusive_and_never_partial_credit(self):
        f1 = _outcomes(_INVENTORY, _TWO, _plan(), _evidence("partial"))["F1"]
        assert f1["status"] == INCONCLUSIVE
        assert "65 of 100" in f1["reason"]

    def test_no_authors_result_set_leaves_the_count_without_a_verdict(self):
        f1 = _outcomes(_INVENTORY, _TWO, _plan(), _evidence(None))["F1"]
        assert f1["status"] == INCONCLUSIVE

    def test_a_concordance_that_could_not_be_computed_is_inconclusive(self):
        f1 = _outcomes(_INVENTORY, _TWO, _plan(), _evidence("not_computed"))["F1"]
        assert f1["status"] == INCONCLUSIVE

    def test_an_analysis_that_did_not_apply_the_claims_definition_is_not_conclusive(self):
        f1 = _outcomes(_INVENTORY, _TWO, _plan(), _evidence("agree", applied=False))["F1"]
        assert f1["status"] == INCONCLUSIVE
        assert "statistical definition" in f1["reason"]

    def test_a_stale_result_never_contributes(self):
        # Computed for revision 1; the selection is now at revision 2.
        f1 = _outcomes(_INVENTORY, _TWO, _plan(revision=2), _evidence("agree", stamp=1))["F1"]
        assert f1["status"] != SUPPORTED

    def test_a_result_for_another_claim_is_not_this_findings(self):
        outcomes = _outcomes(_INVENTORY, _TWO, _plan(claim=1), _evidence("agree", claim=1))
        assert outcomes["F1"]["status"] == NOT_ATTEMPTED
        assert outcomes["F2"]["status"] == SUPPORTED


class TestOneFindingCountsOnce:
    """F1 is established by two peak counts (two antibodies, say), each compared as a finding-tier metric."""

    def _f1(self, first, second):
        targets = [
            {
                "id": 31,
                "claim_text": "peaks, antibody one",
                "metric_key": "peak_count",
                "claimed_value": 9,
                "checks": _SCALAR,
            },
            {
                "id": 32,
                "claim_text": "peaks, antibody two",
                "metric_key": "peak_count",
                "claimed_value": 8,
                "checks": _SCALAR,
            },
        ]
        evidence = {
            "comparison_targets": [
                {"id": 31, "claim_text": "peaks, antibody one", "metric_key": "peak_count"},
                {"id": 32, "claim_text": "peaks, antibody two", "metric_key": "peak_count"},
            ],
            "classification_result": {
                "comparisons": [
                    {"metric_key": "peak_count", "mapped_key": "peak_count", "verdict": first, "advisory": False},
                    {"metric_key": "peak_count", "mapped_key": "peak_count", "verdict": second, "advisory": False},
                ],
                "attribution": {"our_side": "cleared", "reasons": []},
                "divergence_attribution": {},
            },
        }
        inventory = _inventory(_finding("F1", "primary", [0, 1]))
        return _outcomes(inventory, targets, {}, evidence)["F1"]

    def test_every_required_claim_supported_is_one_supported_finding(self):
        f1 = self._f1("agree", "agree")
        assert f1["status"] == SUPPORTED
        assert len(f1["subchecks"]) == 2

    def test_a_required_claim_left_unassessed_is_inconclusive(self):
        f1 = self._f1("agree", "not_computed")
        assert f1["status"] == INCONCLUSIVE
        assert "1 of 2" in f1["reason"]

    def test_mixed_subchecks_are_inconclusive(self):
        assert self._f1("agree", "diverge")["status"] == INCONCLUSIVE

    def test_every_required_claim_discrepant_is_a_discrepancy(self):
        assert self._f1("diverge", "diverge")["status"] == DISCREPANCY


class TestQCComparisons:
    def _scalar(self, row, *, attribution=None, divergence_attribution=None, category="supporting"):
        target = {
            "id": 11,
            "claim_text": "we called many peaks",
            "metric_key": "peak_count",
            "claimed_value": 9000,
            "checks": _SCALAR,
        }
        evidence = {
            "comparison_targets": [{"id": 11, "claim_text": "we called many peaks", "metric_key": "peak_count"}],
            "classification_result": {
                "comparisons": [row],
                "attribution": attribution or {"our_side": "n/a", "reasons": []},
                "divergence_attribution": divergence_attribution or {},
            },
        }
        inventory = _inventory(_finding("F1", category, [0]))
        plan = {"analysis_selection": {"current": {"revision": 1, "claim_index": 0, "check": "qc_metric"}}}
        return _outcomes(inventory, [target], plan, evidence)["F1"]

    def _row(self, verdict, mapped="peak_count", advisory=False):
        return {
            "metric_key": "peak_count",
            "mapped_key": mapped,
            "verdict": verdict,
            "advisory": advisory,
            "advisory_reason": "a different basis" if advisory else None,
        }

    def test_a_finding_tier_metric_that_agrees_supports_the_finding(self):
        f1 = self._scalar(self._row("agree"))
        assert f1["status"] == SUPPORTED
        assert f1["assessment_method"] == "qc_metric"

    def test_a_finding_tier_divergence_the_classifier_cleared_is_a_discrepancy(self):
        f1 = self._scalar(self._row("diverge"), attribution={"our_side": "cleared", "reasons": []})
        assert f1["status"] == DISCREPANCY

    def test_a_divergence_a_named_tool_difference_explains_is_inconclusive(self):
        f1 = self._scalar(
            self._row("diverge"),
            attribution={"our_side": "cleared", "reasons": []},
            divergence_attribution={"peak_count": {"explanation": "the tools differ"}},
        )
        assert f1["status"] == INCONCLUSIVE
        assert "the tools differ" in f1["reason"].lower()

    def test_an_advisory_comparison_is_inconclusive(self):
        f1 = self._scalar(self._row("agree", advisory=True))
        assert f1["status"] == INCONCLUSIVE
        assert "a different basis" in f1["reason"]

    def test_technical_qc_never_earns_a_scoreable_finding_credit(self):
        f1 = self._scalar(self._row("agree", mapped="total_sequences"))
        assert f1["status"] not in (SUPPORTED, DISCREPANCY)
        assert any("QC" in c["text"] for c in f1["supporting_checks"])

    def test_technical_qc_is_the_result_of_a_technical_check(self):
        f1 = self._scalar(self._row("agree", mapped="total_sequences"), category="technical")
        assert f1["status"] == SUPPORTED

    def test_rows_are_matched_to_claims_by_content_when_no_id_was_recorded(self):
        target = {
            "claim_text": "we called many peaks",
            "metric_key": "peak_count",
            "claimed_value": 9000,
            "checks": _SCALAR,
        }
        evidence = {
            "comparison_targets": [
                {"claim_text": "reads", "metric_key": "total_sequences", "claimed_value": 1},
                {"claim_text": "we called many peaks", "metric_key": "peak_count", "claimed_value": 9000},
            ],
            "classification_result": {
                "comparisons": [self._row("agree", mapped="total_sequences"), self._row("agree")],
                "attribution": {"our_side": "n/a", "reasons": []},
                "divergence_attribution": {},
            },
        }
        outcomes = _outcomes(_inventory(_finding("F1", "supporting", [0])), [target], {}, evidence)
        assert outcomes["F1"]["status"] == SUPPORTED


class TestAFailedPrerequisiteInvalidatesWhatDependsOnIt:
    def test_a_supported_finding_becomes_unresolved(self):
        targets = [
            _target("genes up", checks=_DE_AVAILABLE),
            {
                "id": 21,
                "claim_text": "reads per library",
                "metric_key": "total_sequences",
                "claimed_value": 3,
                "checks": _SCALAR,
            },
        ]
        inventory = _inventory(
            _finding("F1", "primary", [0]),
            _finding("T1", "technical", [1], prerequisite_for=["F1"]),
        )
        evidence = _evidence("agree")
        evidence["comparison_targets"] = [
            {"id": 21, "claim_text": "reads per library", "metric_key": "total_sequences"}
        ]
        evidence["classification_result"] = {
            "comparisons": [
                {
                    "metric_key": "total_sequences",
                    "mapped_key": "total_sequences",
                    "verdict": "diverge",
                    "advisory": False,
                }
            ],
            "attribution": {"our_side": "suspected", "reasons": []},
            "divergence_attribution": {},
        }
        outcomes = _outcomes(inventory, targets, _plan(), evidence)
        assert outcomes["T1"]["status"] == DISCREPANCY
        assert outcomes["F1"]["status"] == UNRESOLVED
        assert "finding t1 did not pass" in outcomes["F1"]["reason"].lower()


class TestAuthorResultConsistencyIsNeverIndependent:
    def test_agreement_with_the_authors_table_is_shown_and_earns_nothing(self):
        consistency = {
            0: {"outcome": "agree", "label": "Consistent with the authors' deposited results", "table": "t.txt.gz"}
        }
        outcomes = _outcomes(_INVENTORY, _TWO, _plan(claim=1), {}, consistency=consistency)
        f1 = outcomes["F1"]
        assert f1["status"] not in (SUPPORTED, DISCREPANCY)
        assert f1["supporting_checks"] == [
            {"kind": "author_results", "text": "Consistent with the authors' deposited results (t.txt.gz)"}
        ]


class TestWhyAFindingHasNoConclusiveResult:
    def test_an_unselected_finding_with_available_checks_was_not_attempted(self):
        f2 = _outcomes(_INVENTORY, _TWO, _plan(claim=0), {})["F2"]
        assert f2["status"] == NOT_ATTEMPTED
        assert f2["reason"] == "Not selected for this run; its checks are available."

    def test_controlled_access_is_blocked_with_no_negative_conclusion(self):
        targets = [_target("genes up", checks=_DE_CONTROLLED), _target("genes down", checks=_DE_CONTROLLED)]
        f1 = _outcomes(_INVENTORY, targets, {}, {})["F1"]
        assert f1["status"] == BLOCKED
        assert f1["cause"] == "access"
        assert f1["cause_label"] == "Access required"
        assert f1["reason"].startswith("Access required; no negative scientific conclusion.")
        assert "controlled access" in f1["reason"]

    def test_data_not_deposited_is_blocked_and_says_what_is_missing(self):
        checks = copy.deepcopy(_DE_CONTROLLED)
        checks["raw_reanalysis"] = _check(
            "unavailable", "no deposit of this experiment publishes raw reads", "raw_reads"
        )
        f1 = _outcomes(_INVENTORY, [_target("up", checks=checks), _target("down", checks=checks)], {}, {})["F1"]
        assert (f1["status"], f1["cause"]) == (BLOCKED, "not_deposited")

    def test_a_reference_bioaf_cannot_supply_is_a_bioaf_limitation(self):
        checks = copy.deepcopy(_DE_CONTROLLED)
        checks["processed_reanalysis"] = _check(
            "unavailable", "no processed matrix holding both arms is published", "processed_matrix"
        )
        checks["raw_reanalysis"] = _check("unavailable", "bioAF cannot supply mm9 to nf-core/rnaseq", "reference")
        checks["processed_reanalysis"]["requirement"] = "workflow"
        f1 = _outcomes(_INVENTORY, [_target("up", checks=checks), _target("down", checks=checks)], {}, {})["F1"]
        assert (f1["status"], f1["cause"]) == (BLOCKED, "bioaf_limitation")

    def test_an_unresolved_requirement_is_unresolved(self):
        checks = copy.deepcopy(_DE_CONTROLLED)
        checks["raw_reanalysis"] = _check(
            "unresolved", "the paper's statements of its assembly contradict each other", "reference"
        )
        f1 = _outcomes(_INVENTORY, [_target("up", checks=checks), _target("down", checks=checks)], {}, {})["F1"]
        assert f1["status"] == UNRESOLVED

    def test_the_selected_finding_takes_the_runs_governing_limitation(self):
        evidence = {
            "completion": {
                "limitations": [
                    {
                        "kind": "sample_mapping_unresolved",
                        "detail": "no column could be matched to KO",
                        "operation": "deposit",
                    }
                ]
            }
        }
        f1 = _outcomes(_INVENTORY, _TWO, _plan(claim=0), evidence)["F1"]
        assert f1["status"] == UNRESOLVED
        assert "no column could be matched to KO" in f1["reason"]

    def test_the_selected_finding_blocked_by_access_says_so(self):
        evidence = {"completion": {"limitations": [{"kind": "controlled_access", "resource": "EGAS1"}]}}
        f1 = _outcomes(_INVENTORY, _TWO, _plan(claim=0), evidence)["F1"]
        assert (f1["status"], f1["cause"]) == (BLOCKED, "access")

    def test_a_failed_reanalysis_is_a_bioaf_failure_left_unresolved(self):
        evidence = {"level3_failed": {"reason": "the notebook stopped"}}
        f1 = _outcomes(_INVENTORY, _TWO, _plan(claim=0), evidence)["F1"]
        assert f1["status"] == UNRESOLVED
        assert "the notebook stopped" in f1["reason"]

    def test_a_study_still_running_has_not_attempted_its_finding_yet(self):
        f1 = _outcomes(_INVENTORY, _TWO, _plan(claim=0), {}, study={"state": "running"})["F1"]
        assert f1["status"] == NOT_ATTEMPTED
        assert "in progress" in f1["reason"]

    def test_a_refused_selection_says_why_nothing_was_checked(self):
        plan = {
            "analysis_selection": {
                "current": None,
                "refusal": {"outcome": "no_compatible_contrast", "reason": "nf-core/chipseq cannot analyze RNA-seq"},
            }
        }
        f1 = _outcomes(_INVENTORY, _TWO, plan, {})["F1"]
        assert f1["status"] == NOT_ATTEMPTED
        assert "nf-core/chipseq cannot analyze RNA-seq" in f1["reason"]

    def test_a_legacy_claim_with_no_checks_was_not_attempted(self):
        f1 = _outcomes(_INVENTORY, [_target("up"), _target("down")], {}, {})["F1"]
        assert f1["status"] == NOT_ATTEMPTED

    @pytest.mark.parametrize("state, status", [("plan_declined", NOT_ATTEMPTED), ("error", UNRESOLVED)])
    def test_a_declined_or_errored_study(self, state, status):
        f1 = _outcomes(_INVENTORY, _TWO, _plan(claim=0), {}, study={"state": state})["F1"]
        assert f1["status"] == status
