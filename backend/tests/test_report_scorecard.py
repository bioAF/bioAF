"""plan_8 sections 6 and 8: the Validation Scorecard in the report projection, and the acceptance cases.

The projection is what the page, the JSON export and the markdown export render, so the scorecard is
built there once. A study read before the inventory existed gets no score at all: its evidence cannot be
associated with reviewed findings, and nothing is inferred from its classification.

Groff and SAMD1 are fixtures that exposed defects, never specifications. Their identifiers, counts and
labels stay in this test data.
"""

import json
import pathlib

import pytest

from app.services.validation_checks import evaluate_checks
from app.services.validation_finding_inventory import inventory_from_proposal
from app.services.validation_report_summary import summarize
from tests.test_report_summary import _STUDY_34, _groff_failed_evidence

_SAMD1 = json.loads((pathlib.Path(__file__).parent / "fixtures" / "samd1" / "study_37_persisted.json").read_text())
_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]


def _summary(*, plan, targets, evidence=None, state="classified", classification="inconclusive"):
    return summarize(
        study={"state": state, "classification": classification},
        evidence=evidence or {},
        plan=plan,
        targets=targets,
        issues=[],
    )


class TestAStudyReadBeforeTheInventory:
    def test_study_34_gets_no_fabricated_score(self):
        card = _summary(
            plan=_STUDY_34["reproduction_plan"],
            targets=_STUDY_34["reproduction_plan"]["comparison_targets"],
            evidence=_STUDY_34["evidence"],
            classification=_STUDY_34["study"]["classification"],
        )["scorecard"]
        assert card["status"] == "unavailable"
        assert card["status_label"] == "Score unavailable for this historical report."
        assert card["score"] is None and card["total_count"] is None

    def test_a_validated_classification_is_never_turned_into_a_score(self):
        evidence = {
            "level3": {"claim_index": 0, "cutoffs": {"significance": {"kind": "padj", "operator": "<", "value": 0.05}}},
            "level3_result": {"concordance": {"verdict": "agree", "paper_n": 10, "concordant": 9}},
            "classification_result": {"classification": "validated", "comparisons": []},
        }
        card = _summary(
            plan={"differential_design": {"contrasts": [{"name": "t vs c"}]}},
            targets=[{"claim_text": "genes", "contrast_index": 0}],
            evidence=evidence,
            classification="validated",
        )["scorecard"]
        assert card["status"] == "unavailable"
        assert card["display_score"] is None

    def test_the_samd1_claim_count_is_not_a_reviewed_denominator(self):
        plan = _SAMD1["reproduction_plan"]
        card = _summary(plan=plan, targets=plan["comparison_targets"], evidence=_SAMD1["evidence"])["scorecard"]
        assert len(plan["comparison_targets"]) > 0
        assert card["total_count"] is None
        assert card["status"] == "unavailable"


class TestAStudyWithNoPlanYet:
    def test_a_study_still_being_read_is_in_progress_and_not_established(self):
        card = _summary(plan={}, targets=[], state="reading")["scorecard"]
        assert card["status"] == "not_established"
        assert card["in_progress"] is True
        assert card["reason"]


def _groff_plan_and_targets(evidence):
    """A fresh Groff read on the current build: the differential claims on their contrast, the sample
    count and the depth as technical checks, and the raw reads under controlled access."""
    deposits = evidence["capabilities"]["deposits"]
    experiment = {
        "id": "e1",
        "assay": "bulk RNA-seq",
        "workflow": "nf-core/rnaseq",
        "reference": {"assembly": {"stated": "GRCh38", "status": "usable", "resolved": "GRCh38"}},
    }
    raw = [dict(t) for t in _STUDY_34["reproduction_plan"]["comparison_targets"]]
    for target in raw:
        target["reported_experiment_id"] = "e1"
        if target["metric_key"] == "de_gene_count":
            target.update(contrast_index=0, cutoffs=[{"kind": "padj", "operator": "<", "value": 0.05}])
    contrast = {"name": "aneuploid vs euploid", "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}]}
    for target in raw:
        target["checks"] = evaluate_checks(
            target,
            experiment=experiment,
            resources=[],
            deposits=deposits,
            supplements=evidence["supplements"],
            contrast=contrast if target.get("contrast_index") == 0 else None,
        )
    text = " ".join(t["claim_text"] for t in raw)
    de = [i for i, t in enumerate(raw) if t["metric_key"] == "de_gene_count"]
    others = [i for i, t in enumerate(raw) if t["metric_key"] != "de_gene_count"]
    proposal = [
        {
            "description": "Differential expression between embryo groups",
            "claim_indices": [de[0]],
            "importance": "primary",
            "rationale": "The paper's main conclusion rests on it.",
            "quote": raw[de[0]]["claim_text"],
        },
        *[
            {
                "description": f"Differences in group {n}",
                "claim_indices": [i],
                "importance": "supporting",
                "rationale": "It qualifies the main conclusion.",
                "quote": raw[i]["claim_text"],
            }
            for n, i in enumerate(de[1:], start=2)
        ],
        {
            "description": "Samples, depth and detected transcripts",
            "claim_indices": others,
            "importance": "technical",
            "rationale": "They describe the data set and establish no finding.",
        },
    ]
    inventory = inventory_from_proposal(proposal, targets=raw, full_text=text, decided_by={"kind": "model"})
    plan = {
        "differential_design": {"contrasts": [contrast]},
        "reported_experiments": [experiment],
        "finding_inventory": inventory,
    }
    return plan, raw


class TestGroff:
    """Controlled access yields no failing score and no scientific discrepancy."""

    @pytest.mark.asyncio
    async def test_controlled_access_is_no_score_and_no_discrepancy(self):
        evidence = await _groff_failed_evidence()
        plan, targets = _groff_plan_and_targets(evidence)
        assert plan["finding_inventory"]["status"] == "established"
        card = _summary(plan=plan, targets=targets, evidence=evidence, classification="access_restricted")["scorecard"]
        assert card["status"] == "not_assessed"
        assert card["score"] is None
        assert card["scope_label"] == f"0 / {card['total_count']} assessed"
        assert card["discrepant_count"] == 0
        causes = {item["cause"] for item in card["unassessed_items"]}
        assert causes == {"access"}
        for item in card["unassessed_items"]:
            assert item["reason"].startswith("Access required; no negative scientific conclusion.")

    @pytest.mark.asyncio
    async def test_the_scorecard_never_repeats_the_missing_data_conclusion(self):
        evidence = await _groff_failed_evidence()
        plan, targets = _groff_plan_and_targets(evidence)
        card = _summary(plan=plan, targets=targets, evidence=evidence, classification="missing_data")["scorecard"]
        words = json.dumps(card).lower()
        assert "missing data" not in words
        assert "required input not published" not in words

    @pytest.mark.asyncio
    async def test_the_technical_checks_are_listed_and_not_scored(self):
        evidence = await _groff_failed_evidence()
        plan, targets = _groff_plan_and_targets(evidence)
        card = _summary(plan=plan, targets=targets, evidence=evidence)["scorecard"]
        [excluded] = card["excluded_items"]
        assert excluded["description"] == "Samples, depth and detected transcripts"
        assert excluded["weight"] == 0


def _samd1_like():
    """Two differential counts on one contrast, one finding, the selected claim checked against the
    authors' table and never reanalysed (the mapping was unresolved)."""
    targets = [
        {
            "claim_text": "genes up",
            "claimed_value": 257,
            "contrast_index": 0,
            "cutoffs": _P,
            "direction": "up",
            "checks": {
                "processed_reanalysis": {
                    "status": "unresolved",
                    "reason": "the sample mapping is established when the input is chosen",
                    "requirement": "sample_mapping",
                }
            },
        },
        {
            "claim_text": "genes down",
            "claimed_value": 524,
            "contrast_index": 0,
            "cutoffs": _P,
            "direction": "down",
            "checks": {
                "processed_reanalysis": {
                    "status": "unresolved",
                    "reason": "the sample mapping is established when the input is chosen",
                    "requirement": "sample_mapping",
                }
            },
        },
    ]
    proposal = [
        {
            "description": "Loss of the gene deregulates hundreds of genes",
            "claim_indices": [0, 1],
            "importance": "primary",
            "rationale": "The main conclusion.",
            "quote": "genes up",
        }
    ]
    plan = {
        "differential_design": {"contrasts": [{"name": "KO vs WT", "cutoffs": _P}]},
        "analysis_selection": {
            "current": {"revision": 1, "claim_index": 0, "check": "processed_reanalysis", "contrast_index": 0}
        },
        "finding_inventory": inventory_from_proposal(
            proposal, targets=targets, full_text="genes up and genes down", decided_by={"kind": "model"}
        ),
    }
    evidence = {
        "author_consistency": {
            "records": [
                {"claim_index": 0, "outcome": "agree", "table": "deseq2.txt.gz", "rows_passing": 257},
                {"claim_index": 1, "outcome": "agree", "table": "deseq2.txt.gz", "rows_passing": 524},
            ]
        },
        "completion": {
            "limitations": [{"kind": "sample_mapping_unresolved", "detail": "no column could be matched to KO"}]
        },
    }
    return plan, targets, evidence


class TestSAMD1AuthorResultChecks:
    def test_agreement_with_the_published_results_does_not_increase_supported_or_assessed(self):
        plan, targets, evidence = _samd1_like()
        card = _summary(plan=plan, targets=targets, evidence=evidence)["scorecard"]
        assert (card["supported_count"], card["assessed_count"], card["total_count"]) == (0, 0, 1)
        assert card["score"] is None
        [item] = card["unassessed_items"]
        assert item["status"] == "unresolved"
        assert {
            "kind": "author_results",
            "text": "Consistent with the authors' deposited results (deseq2.txt.gz)",
        } in item["supporting_checks"]
        assert card["messages"] == [
            {"kind": "primary_unassessed", "text": "Primary finding remains unassessed", "findings": ["F1"]}
        ]


class TestTheListsReconcileWithTheMetrics:
    def test_items_reconcile_with_the_numerator_and_denominator(self):
        plan, targets, evidence = _samd1_like()
        evidence = {
            **evidence,
            "level3": {"claim_index": 0, "source": "deposit", "cutoffs": {"significance": _P[0]}},
            "level3_result": {
                "concordance": {"verdict": "agree", "paper_n": 10, "concordant": 9, "enrichment_p": 1e-9}
            },
            "completion": {},
        }
        card = _summary(plan=plan, targets=targets, evidence=evidence)["scorecard"]
        # Claim 1 was never reanalysed, so the finding is incomplete: one of two required claims.
        assert card["assessed_count"] == len(card["assessed_items"]) == 0
        assert len(card["unassessed_items"]) == card["total_count"] == 1
        assert card["unassessed_items"][0]["status"] == "inconclusive"

    def test_an_active_study_is_marked_in_progress(self):
        plan, targets, evidence = _samd1_like()
        card = _summary(plan=plan, targets=targets, evidence={}, state="comparing")["scorecard"]
        assert card["in_progress"] is True


# ---- scenarios the frontend contract renders ------------------------------------------------------------

_SCALAR_CHECKS = {
    "qc_metric": {"status": "available", "reason": None, "requirement": None},
    "author_results": {
        "status": "unavailable",
        "reason": "no deposited or attached file measures this quantity",
        "requirement": "measured_file",
    },
    "processed_reanalysis": {
        "status": "unavailable",
        "reason": "bioAF computes no QC metric from processed files",
        "requirement": "not_applicable",
    },
    "raw_reanalysis": {
        "status": "available",
        "reason": "the same run as the QC metric comparison; not a separate check (available)",
        "requirement": None,
    },
}


def qc_scenario(verdicts: list[str], categories: list[str], *, state: str = "classified") -> dict:
    """One scalar claim per finding, each compared as a finding-tier metric with the given verdict.
    ``not_computed`` leaves a finding without a result. Rendered through the real projection."""
    targets = [
        {
            "id": i + 1,
            "claim_text": f"sample {i + 1} yielded many peaks",
            "metric_key": "peak_count",
            "claimed_value": 1000 + i,
            "unit": "peaks",
            "bound_key": "peak_count",
            "bound_by": "model",
            "checks": _SCALAR_CHECKS,
        }
        for i in range(len(verdicts))
    ]
    text = " ".join(str(t["claim_text"]) for t in targets)
    proposal = [
        {
            "description": f"Binding at the sites of sample {i + 1}",
            "claim_indices": [i],
            "importance": category,
            "rationale": "Its role in the paper's conclusions.",
            "quote": targets[i]["claim_text"],
        }
        for i, category in enumerate(categories)
    ]
    plan = {
        "finding_inventory": inventory_from_proposal(
            proposal, targets=targets, full_text=text, decided_by={"kind": "model", "model": "m"}
        ),
        "analysis_selection": {
            "current": None,
            "unassessed": [
                {"claim_index": i, "reason": "not selected for this run; its checks are available"}
                for i in range(len(targets))
            ],
        },
    }
    evidence = {
        "comparison_targets": [
            {"id": t["id"], "claim_text": t["claim_text"], "metric_key": t["metric_key"]} for t in targets
        ],
        "classification_result": {
            "comparisons": [
                {
                    "metric_key": "peak_count",
                    "mapped_key": "peak_count" if v != "not_computed" else None,
                    "verdict": v,
                    "advisory": False,
                }
                for v in verdicts
            ],
            "attribution": {"our_side": "cleared", "reasons": []},
            "divergence_attribution": {},
        },
    }
    return _summary(plan=plan, targets=targets, evidence=evidence, state=state)


def scored_example() -> dict:
    """plan_8's first example: four supporting findings supported, one primary finding discrepant."""
    return qc_scenario(["agree", "agree", "agree", "agree", "diverge"], ["supporting"] * 4 + ["primary"])


def long_example() -> dict:
    """Two findings supported out of fourteen, a primary among the unassessed."""
    return qc_scenario(
        ["agree", "agree"] + ["not_computed"] * 12, ["supporting", "supporting", "primary"] + ["supporting"] * 11
    )


class TestThePlansExamplesThroughTheProjection:
    def test_four_supported_and_one_primary_discrepant_is_67_of_100_and_5_of_5(self):
        card = scored_example()["scorecard"]
        assert (card["score_label"], card["scope_label"]) == ("67 / 100", "5 / 5 assessed")
        assert card["summary"] == "Four supporting findings supported; one primary finding discrepant."
        assert card["messages"][0]["text"] == "Primary discrepancy: Binding at the sites of sample 5"
        assert card["assessed_items"][0]["finding_id"] == "F5"

    def test_two_supported_out_of_fourteen_is_100_and_2_of_14(self):
        card = long_example()["scorecard"]
        assert (card["score_label"], card["scope_label"]) == ("100 / 100", "2 / 14 assessed")
        assert card["primary_unassessed_count"] == 1
        assert card["unassessed_items"][0]["category"] == "primary"


class TestTheScorecardVocabulariesAreInTheContract:
    def test_every_status_category_and_cause_has_a_label_both_stacks_read(self):
        from app.services.validation_finding_outcomes import CAUSE_LABELS
        from app.services.validation_report_summary import enum_labels
        from app.services.validation_scorecard import CATEGORY_LABELS, SCORECARD_STATUS_LABELS, STATUS_LABELS

        enums = enum_labels()
        assert enums["finding_status"] == STATUS_LABELS
        assert enums["finding_category"] == CATEGORY_LABELS
        assert enums["finding_cause"] == CAUSE_LABELS
        assert set(enums["scorecard_status"]) == set(SCORECARD_STATUS_LABELS)
