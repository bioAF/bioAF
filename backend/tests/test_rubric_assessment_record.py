"""plan_8_5 section 3.2: an assessment is produced once, persisted, and read back.

Rendering a report must not judge anything. The obligations are settled where the evidence is
gathered, each outcome is stored under its own leaf with what it was read from, and every surface
projects that stored revision. Two surfaces cannot then disagree, and an outcome a model produced
survives a page refresh instead of being asked again.
"""

from fractions import Fraction

from app.services.validation_rubric_assessment import (
    CHECKER_VERSION,
    assessment_inputs,
    build_assessment,
    card_from,
    reusable,
)

_PLAN = {
    "reported_experiments": [
        {
            "id": "e1",
            "assay": "bulk RNA-seq",
            "organism": "Homo sapiens",
            "workflow": "nf-core/rnaseq",
            "reference": {
                "assembly": {"stated": "hg19", "resolved": "GRCh37", "status": "usable"},
                "annotation": {"stated": "GENCODE v19", "resolved": "GENCODE v19", "status": "usable"},
            },
        }
    ],
    "differential_design": {
        "contrasts": [
            {
                "name": "KO vs WT",
                "test_condition": "KO",
                "reference_condition": "WT",
                "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}],
            }
        ]
    },
    "sample_sheet": {"organism": "Homo sapiens", "sample_count": 4},
}


def _built(**kw):
    return build_assessment(
        plan=kw.pop("plan", _PLAN),
        evidence=kw.pop("evidence", {}),
        claims=kw.pop("claims", []),
        inventory=kw.pop("inventory", None),
        **kw,
    )


class TestWhatOneAssessmentHolds:
    def test_every_allocated_obligation_is_stored_under_its_own_leaf(self):
        record = _built()
        assert record["outcomes"]["S1.A"]["outcome"] == "verified"
        assert record["outcomes"]["S1.A"]["rationale"]
        assert record["outcomes"]["S1.A"]["method"] == "measurement"

    def test_the_allocation_it_was_scored_under_travels_with_it(self):
        record = _built()
        weights = {leaf["id"]: Fraction(leaf["weight"]) for leaf in record["leaves"]}
        assert sum(weights.values()) == 100
        assert record["profile"]["revision"]

    def test_it_records_which_checker_produced_it(self):
        assert _built()["checker_version"] == CHECKER_VERSION
        assert _built()["at"]
        assert _built()["revision"] == 1

    def test_it_is_json_safe_so_it_can_be_persisted_as_it_stands(self):
        import json

        assert json.loads(json.dumps(_built()))["rubric_version"] == 3


class TestTheCardIsAProjectionOfWhatWasStored:
    def test_the_card_comes_from_the_stored_outcomes_and_nothing_else(self):
        card = card_from(_built(), reproduction={"attempted": False, "label": "Independent reproduction: not attempted"})
        assert card["score"] + card["failed"] + card["undetermined"] == 100
        assert card["score"] > 0

    def test_a_stored_outcome_is_what_the_card_shows_even_where_the_evidence_moved_on(self):
        """The snapshot is the record. Re-reading a report never rescores old evidence."""
        record = _built()
        record["outcomes"]["S1.A"] = {"outcome": "failed", "rationale": "recorded that way", "scope": "s"}
        card = card_from(record, reproduction=None)
        rows = {
            row["leaf"]: row
            for section in card["sections"]
            for criterion in section["criteria"]
            for row in criterion["obligations"]
        }
        assert rows["S1.A"]["outcome"] == "failed"


class TestReuseAndInvalidation:
    def test_an_unchanged_study_reuses_its_assessment(self):
        record = _built()
        assert reusable(record, assessment_inputs(plan=_PLAN, evidence={}, claims=[], inventory=None))

    def test_changed_evidence_invalidates_it(self):
        record = _built()
        moved = assessment_inputs(
            plan=_PLAN,
            evidence={"precompute_checks": {"species_matches": {"verdict": "mismatch"}}},
            claims=[],
            inventory=None,
        )
        assert not reusable(record, moved)

    def test_a_changed_checker_invalidates_it_even_where_the_evidence_is_identical(self):
        """A changed checker must not reuse the old checker's accepted outcome."""
        record = {**_built(), "checker_version": CHECKER_VERSION - 1}
        assert not reusable(record, assessment_inputs(plan=_PLAN, evidence={}, claims=[], inventory=None))

    def test_evidence_the_assessment_never_reads_does_not_invalidate_it(self):
        record = _built()
        noise = assessment_inputs(
            plan=_PLAN, evidence={"retrieval_ledger": [{"at": "later"}]}, claims=[], inventory=None
        )
        assert reusable(record, noise)


class TestTheJudgmentsAreInputsToo:
    """plan_8_5 section 3.6, caught live on study 62: seventeen judgments were made and accepted,
    and the score did not move. Reuse is keyed on what the checks read, and the judgments were not
    in that key, so the assessment was reused and the answers never reached an obligation."""

    _JUDGED = {
        "rubric_judgments": {
            "judgments": {
                "E1.A": {
                    "outcome": "verified",
                    "rationale": "the methods state the procedure",
                    "scope": "23 supplied passages",
                    "method": "model_assisted",
                    "evidence": {"citations": ["c2"]},
                }
            }
        }
    }

    def test_a_new_judgment_changes_the_inputs(self):
        before = assessment_inputs(plan=_PLAN, evidence={}, claims=[], inventory=None)
        after = assessment_inputs(plan=_PLAN, evidence=self._JUDGED, claims=[], inventory=None)
        assert before != after

    def test_a_held_assessment_is_not_reused_once_a_judgment_arrives(self):
        record = _built()
        assert not reusable(record, assessment_inputs(plan=_PLAN, evidence=self._JUDGED, claims=[], inventory=None))

    def test_the_judged_obligation_then_earns_its_points(self):
        before = _built()
        after = _built(evidence=self._JUDGED)
        assert before["outcomes"]["E1.A"]["outcome"] == "undetermined"
        assert after["outcomes"]["E1.A"]["outcome"] == "verified"

    def test_a_recorded_paper_statement_is_an_input_as_well(self):
        stated = {"paper_statements": {"sample_material": {"value": "HepG2 cells", "quote": "from HepG2 cells"}}}
        assert assessment_inputs(plan=_PLAN, evidence=stated, claims=[], inventory=None) != assessment_inputs(
            plan=_PLAN, evidence={}, claims=[], inventory=None
        )
