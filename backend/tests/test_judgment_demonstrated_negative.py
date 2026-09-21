"""plan_8_6 section 8: what exempts a negative from coverage is what it SHOWED, not how it read.

The owner, reviewing the deployed code, 2026-09-21:

    "The negative validator still uses the keyword approach the plan explicitly rejected. Words such
    as whereas, inconsistent, and while the can exempt a negative from the evidence-coverage
    requirement. Distinguish a demonstrated defect from insufficient verification using the
    supporting observations and their scope. Wording alone must not bypass coverage."

`needs_coverage` ran two regexes over the prose: one that recognised an absence claim and one that
recognised a "positive finding". Either could be dodged or tripped by wording alone, so the same
finding was gated or exempted depending on which words the assessor happened to choose:

    unmet, "The correction is not stated in the paper."                     -> untested
    unmet, the same sentence + "whereas the test is named."                 -> negative
    unmet, "The fitness of this operation is not established by the cited
            evidence."                                                      -> negative

A defect is DEMONSTRATED when the assessor can point at what two supplied passages each state and
say what about them disagrees. That is a structure bioAF can check against the evidence it supplied:
the observations name passages it carried, and the finding names the scope it is about. An answer
that cannot point at them is not exempt, however it is phrased, and its absence claim is gated on
the coverage record exactly as before.
"""

import pytest

from app.services.validation_judgment import judgment_from
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED

_PASSAGES = [
    {
        "id": "m1",
        "source": "the paper's methods (Bulk RNA-seq analysis)",
        "text": (
            "PantherDB was used to calculate gene ontology enrichment for significantly upregulated "
            "(log 2 fc >3, p adj < 0.05) genes for each sample relative to hiPSCs."
        ),
    },
    {
        "id": "code:DEG/deg_interpretation.py#1",
        "source": "the supplied DEG/deg_interpretation.py",
        "text": "lines 40-44:\nvisuz.GeneExpression.volcano(df=df, lfc_thr=(1, 1), pv_thr=(0.05, 0.05))",
    },
    {
        "id": "m2",
        "source": "the paper's methods (Statistical analysis)",
        "text": "Counts were modelled with a negative binomial fit and a Wald test.",
    },
]

_INCOMPLETE = {
    "sufficient": False,
    "supplied": ["methods: bulk rna-seq analysis"],
    "unavailable": ["4 of this paper's attachments were not retrieved (transient)"],
    "deferred": [],
    "truncated": False,
    "reason": "4 of this paper's attachments were not retrieved (transient)",
}

_COVERED = {
    "sufficient": True,
    "supplied": ["methods: bulk rna-seq analysis", "methods: statistical analysis"],
    "unavailable": [],
    "deferred": [],
    "truncated": False,
    "reason": "",
}


def _unmet(**kw) -> dict:
    return {
        "outcome": "unmet",
        "rationale": "the stated GO-enrichment threshold and the supplied script's threshold disagree",
        "citations": ["m1", "code:DEG/deg_interpretation.py#1"],
        "scope": "the GO-enrichment input selection for the bulk RNA-seq comparison",
        "impact": "a reader cannot establish which fold-change threshold produced the reported GO terms",
        "confidence": 0.8,
        **kw,
    }


_DEMONSTRATION = [
    {"citation": "m1", "states": "the paper sets the GO-enrichment input at log2 fold change above 3"},
    {
        "citation": "code:DEG/deg_interpretation.py#1",
        "states": "the supplied script sets that same threshold to 1",
    },
]


class TestADemonstratedDefectPointsAtWhatItRestsOn:
    def test_two_observations_on_two_supplied_passages_stand_without_full_coverage(self):
        found = judgment_from(
            "M5.B",
            _unmet(basis="contradiction", observations=_DEMONSTRATION),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == FAILED
        assert found["basis"] == "contradiction"
        assert [o["citation"] for o in found["observations"]] == [
            "m1",
            "code:DEG/deg_interpretation.py#1",
        ]

    def test_a_supplied_procedure_that_does_not_reproduce_the_paper_is_the_same_structure(self):
        found = judgment_from(
            "M5.B",
            _unmet(basis="not_reproduced", observations=_DEMONSTRATION),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == FAILED

    def test_a_method_shown_unsuited_to_what_it_was_applied_to_is_the_same_structure(self):
        found = judgment_from(
            "M3.B",
            _unmet(
                basis="inappropriate",
                rationale="a Wald test is fitted where the cited design supplies one sample per condition",
                citations=["m1", "m2"],
                observations=[
                    {"citation": "m2", "states": "the paper fits a Wald test to the counts"},
                    {"citation": "m1", "states": "each condition contributes one sample"},
                ],
            ),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == FAILED


class TestWordingAloneCannotBuyTheExemption:
    @pytest.mark.parametrize(
        "rationale",
        [
            "The correction is not stated in the paper, whereas the test is named.",
            "The fitness of this operation is not established by the cited evidence.",
            "The methods and the supplied code are inconsistent about the correction.",
            "The correction is not stated, while the test is named.",
            "This is a mismatch between what the paper reports and what was supplied.",
        ],
    )
    def test_a_negative_that_points_at_nothing_is_gated_on_coverage(self, rationale):
        found = judgment_from("M5.B", _unmet(rationale=rationale), passages=_PASSAGES, coverage=_INCOMPLETE)
        assert found["outcome"] == UNDETERMINED, rationale
        assert found["withheld"]["rationale"] == rationale

    def test_one_observation_is_not_a_disagreement(self):
        found = judgment_from(
            "M5.B",
            _unmet(basis="contradiction", observations=_DEMONSTRATION[:1]),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == UNDETERMINED

    def test_two_observations_on_the_SAME_passage_are_not_a_disagreement(self):
        found = judgment_from(
            "M5.B",
            _unmet(
                basis="contradiction",
                observations=[
                    {"citation": "m1", "states": "the paper sets the threshold above 3"},
                    {"citation": "m1", "states": "the paper also mentions an adjusted P below 0.05"},
                ],
            ),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == UNDETERMINED

    def test_an_observation_on_a_passage_nobody_supplied_does_not_count(self):
        found = judgment_from(
            "M5.B",
            _unmet(
                basis="contradiction",
                observations=[
                    {"citation": "m1", "states": "the paper sets the threshold above 3"},
                    {"citation": "supp:never-supplied", "states": "the supplement says otherwise"},
                ],
            ),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == UNDETERMINED

    def test_a_declared_absence_is_gated_however_many_observations_it_names(self):
        found = judgment_from(
            "M5.B",
            _unmet(basis="absent", observations=_DEMONSTRATION),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == UNDETERMINED

    def test_a_finding_that_names_no_scope_is_gated(self):
        """Section 8: the exemption is for a NARROWLY SCOPED negative. One that does not say what it
        is about is a claim of unrecorded reach, and coverage decides it."""
        found = judgment_from(
            "M5.B",
            _unmet(basis="contradiction", observations=_DEMONSTRATION, scope=""),
            passages=_PASSAGES,
            coverage=_INCOMPLETE,
        )
        assert found["outcome"] == UNDETERMINED


class TestCoverageStillSettlesEverythingElse:
    def test_an_ungrounded_negative_stands_where_the_sources_were_inspected(self):
        found = judgment_from(
            "M5.B",
            _unmet(rationale="the correction is not stated in the methods"),
            passages=_PASSAGES,
            coverage=_COVERED,
        )
        assert found["outcome"] == FAILED

    def test_a_truncated_packet_still_stops_an_ungrounded_negative(self):
        found = judgment_from(
            "M5.B",
            _unmet(rationale="the correction is not stated in the methods"),
            passages=_PASSAGES,
            coverage={**_COVERED, "truncated": True},
        )
        assert found["outcome"] == UNDETERMINED

    def test_a_negative_that_undercuts_itself_is_still_a_contradiction(self):
        found = judgment_from(
            "M5.B",
            _unmet(
                basis="contradiction",
                observations=_DEMONSTRATION,
                rationale="the threshold may be stated elsewhere in a supplement bioAF was not given",
            ),
            passages=_PASSAGES,
            coverage=_COVERED,
        )
        assert found["outcome"] == UNDETERMINED
        assert found["conflict"]


class TestTheRequestAsksForTheStructureItIsCheckedOn:
    def test_the_system_prompt_asks_an_unmet_answer_to_point_at_its_observations(self):
        from app.services.validation_judgment import build_request

        system = build_request("M5.B", passages=_PASSAGES)["system"]
        assert "observations" in system
        assert "basis" in system

    def test_the_schema_declares_the_four_kinds_of_defect(self):
        from app.services.validation_judgment import build_request

        schema = build_request("M5.B", passages=_PASSAGES)["schema"]
        assert set(schema["basis"]) == {"absent", "contradiction", "inappropriate", "not_reproduced"}


class TestStudy67sCodeDeductionsMeasuredOnTheDemo:
    """Groff (study 67, 10.1101/gr.252981.119), the owner's POSITIVE control, scored 19/100 against
    study 65's 31 on 2026-09-21. Its supplementary bundle failed three times as `transient`, so
    bioAF held 0 code sources and 0 deposit records, and C5 came out 0 positive, 4 negative, 16
    untested: four points deducted from a paper whose R code bioAF never opened.

    Neither deduction's rationale tripped the old absence regex ("no code content is shown in the
    supplied Supplemental File S2 excerpts" matches none of it), so `needs_coverage` returned False
    and the coverage record, which said in as many words that the code was not retrieved, was never
    consulted. That is the keyword rule in the OTHER direction from the one plan_8_6 section 8 was
    written for, and it is why a paper bioAF read less of scored worse.
    """

    _UNFETCHED = {
        "sufficient": False,
        "supplied": ["methods"],
        "unavailable": [
            "4 of this paper's attachments were not retrieved (transient), so what they carry was not inspected",
            "bioAF holds no source code for this paper, so nothing was read from it",
        ],
        "deferred": [],
        "truncated": False,
        "reason": "bioAF holds no source code for this paper, so nothing was read from it",
    }

    def test_a_deduction_resting_on_code_bioaf_never_opened_is_untested(self):
        found = judgment_from(
            "C5.B",
            {
                "outcome": "unmet",
                "rationale": (
                    "no code content is shown in the supplied Supplemental File S2 excerpts to resolve "
                    "which build was actually run"
                ),
                "citations": ["m1"],
                "scope": "the differential expression analysis of the whole-embryo samples",
                "impact": "a reader cannot establish which build produced the reported results",
                "basis": "absent",
                "confidence": 0.7,
            },
            passages=_PASSAGES,
            coverage=self._UNFETCHED,
        )
        assert found["outcome"] == UNDETERMINED
        assert "not retrieved" in found["rationale"] or "did not inspect" in found["rationale"]

    def test_a_contradiction_the_paper_states_in_its_own_prose_still_stands(self):
        """Not every C5 deduction on that run was bioAF's. The paper states DESeq2 v1.12.4 under R
        3.3.3 in one passage and DESeq2 1.2.0 under R 3.5.1 in another, and that is a demonstrated
        contradiction between two passages bioAF WAS shown. Fixing the first defect must not
        suppress the second."""
        found = judgment_from(
            "C5.A",
            {
                "outcome": "unmet",
                "rationale": "the paper states two incompatible DESeq2 and R version pairings",
                "citations": ["m1", "m2"],
                "scope": "the differential expression analyses of the whole-embryo samples",
                "impact": "a reader cannot establish which build produced the reported gene lists",
                "basis": "contradiction",
                "observations": [
                    {"citation": "m1", "states": "DESeq2 v1.12.4 under R 3.3.3 for the partition analyses"},
                    {"citation": "m2", "states": "DESeq2 1.2.0 under R 3.5.1 for the sex-chromosome analysis"},
                ],
                "confidence": 0.8,
            },
            passages=_PASSAGES,
            coverage=self._UNFETCHED,
        )
        assert found["outcome"] == FAILED
        assert found["basis"] == "contradiction"
