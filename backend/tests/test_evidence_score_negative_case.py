"""plan_8_4 milestone C: a paper beyond the three captured studies, with a confirmed discrepancy.

Everything built so far is demonstrated on papers whose problems are unknowns. A rubric that can only
produce positives and greys is not an honest rubric, so this exercises the other direction: a paper
whose deposited records contradict its stated species, whose supplied code does not parse, and whose
own published table disagrees with the number it reports.

The fixture is constructed, not captured, and nothing about it governs production logic: no DOI, no
study id, no filename and no expected score reaches any decision bioAF makes.
"""

import pytest

from app.services.validation_report_summary import summarize
from app.services.validation_rubric_v3 import FAILED, VERIFIED

_PLAN = {
    "reported_experiments": [
        {
            "id": "e1",
            "assay": "bulk RNA-seq",
            "organism": "Homo sapiens",
            "workflow": "nf-core/rnaseq",
            "reference": {
                "assembly": {"stated": "hg38", "resolved": "GRCh38"},
                "annotation": {"stated": "GENCODE v44", "resolved": "GENCODE v44"},
            },
        }
    ],
    "differential_design": {
        "contrasts": [
            {
                "name": "treated vs control",
                "reported_experiment_id": "e1",
                "test_condition": "treated",
                "reference_condition": "control",
                "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}],
            }
        ]
    },
    "sample_sheet": {"organism": "Homo sapiens", "sample_count": 8},
    "finding_inventory": {
        "rubric_version": 2,
        "findings": [{"id": "F1", "required": [0, 1], "importance": {"category": "primary"}}],
    },
}

_EVIDENCE = {
    # A deterministic comparison: the deposit's records state a different organism from the paper.
    "precompute_checks": {
        "species_matches": {
            "verdict": "mismatch",
            "detail": "the paper states Homo sapiens and the deposit's 8 sample records state Mus musculus",
        }
    },
    "code_inspection": {
        "sources": [
            {
                "path": "differential.py",
                "language": "python",
                "text": "import pandas as pd\n\ndef main(:\n    return pd.DataFrame()\n",
                "provenance": {"from": "supplement", "sha256": "f" * 64},
            }
        ],
        "manifests": [{"path": "requirements.txt", "text": "pandas\n"}],
    },
}

_CLAIMS = [
    {"index": 0, "consistency": {"outcome": "disagree", "reason": "the table holds 412 rows and the paper states 500"}},
    {"index": 1, "consistency": {"outcome": "agree", "reason": "1204 distinct identifiers, as the paper states"}},
]


def _card():
    """The card the report builds, with the claims a real study's targets would project."""
    from app.services.validation_report_summary import evidence_scorecard

    return evidence_scorecard(
        study={"state": "classified", "classification": "discrepancy"},
        evidence=_EVIDENCE,
        plan=_PLAN,
        claims=_CLAIMS,
        attempt={"status": "not_attempted"},
    )


class TestTheNegativePointsAreRealAndNamed:
    def test_the_card_carries_negative_points(self):
        card = _card()
        assert card["failed"] > 0
        assert card["score"] + card["failed"] + card["undetermined"] == 100

    def test_each_confirmed_problem_is_shown_beside_the_score_whatever_its_weight(self):
        concerns = {c["criterion"]: c for c in _card()["concerns"]}
        assert "S1" in concerns, "the species contradiction"
        assert "C1" in concerns, "the code that does not parse"
        assert "R1" in concerns, "the claim its own table disagrees with"
        assert all(c["impact"] for c in concerns.values())

    def test_a_high_score_never_hides_that_the_supplied_code_fails(self):
        code = next(s for s in _card()["sections"] if s["section"] == "C")
        assert code["failed"] > 0
        assert "does not parse" in code["outstanding"]

    def test_what_still_agreed_keeps_its_points(self):
        """A paper with real problems is not a paper with nothing established. The claim that agrees
        keeps its credit and the reference the paper states keeps its own."""
        from app.services.validation_rubric_evidence import assess_evidence

        assessed = assess_evidence(
            plan=_PLAN, evidence=_EVIDENCE, claims=_CLAIMS, inventory=_PLAN["finding_inventory"]
        )
        assert assessed["R1.F1.1"]["outcome"] == VERIFIED
        assert assessed["M2.A"]["outcome"] == VERIFIED
        assert assessed["R1.F1.0"]["outcome"] == FAILED

    def test_zero_verified_is_reachable_and_is_not_dressed_up(self):
        """Section 10: a paper can have 0 verified points. Nothing rounds that into something."""
        card = summarize(
            study={"state": "classified", "classification": "discrepancy"},
            evidence={},
            plan={},
            targets=[],
            issues=[],
            checks=None,
        )["evidence_score"]
        assert card["score"] == 0
        assert card["display"]["verified"] == "0"
        assert card["score_note"] == "Not yet assessed"


class TestNothingAboutTheFixtureGovernsProduction:
    @pytest.mark.parametrize("value", ["differential.py", "Mus musculus", "412", "500", "1204"])
    def test_no_fixture_value_reaches_a_rubric_decision(self, value):
        """Section 8: no DOI, study id, filename, gene count or expected score from a fixture may
        govern production logic. The rubric's own modules are read for each of them."""
        import pathlib

        for module in (
            "validation_rubric_v3.py",
            "validation_rubric_evidence.py",
            "validation_code_checks.py",
            "validation_code_inspection.py",
            "validation_judgment.py",
        ):
            text = pathlib.Path("app/services", module).read_text()
            assert value not in text, f"{module} carries the fixture value {value}"
