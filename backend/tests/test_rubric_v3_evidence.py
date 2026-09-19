"""plan_8_4 section 6.1 and 6.2: the evidence bioAF already holds, mapped to rubric v3's obligations.

The rule the mapping is built on: an obligation is VERIFIED only where held evidence establishes it,
FAILED only where held evidence contradicts it, and UNDETERMINED everywhere else, including every
obligation whose check is not implemented yet. A model's holistic "the methods are detailed enough"
establishes no criterion, an absent extracted value is bioAF's limitation rather than a demonstrated
omission by the paper, and a repository that exists is not code that parses.

These tests are paper-shaped fixtures. Nothing here is a rule about any one paper.
"""

from fractions import Fraction

from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED, allocate, default_profile, score
from app.services.validation_rubric_evidence import CAPABILITY_LIMITS, assess_evidence

_EXPERIMENTS = [
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
]
_CONTRASTS = [
    {
        "name": "XX vs XY whole embryos",
        "reported_experiment_id": "e1",
        "test_condition": "XX whole embryos",
        "reference_condition": "XY whole embryos",
        "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}],
    }
]
_PLAN = {
    "reported_experiments": _EXPERIMENTS,
    "differential_design": {"contrasts": _CONTRASTS},
    "sample_sheet": {"organism": "Homo sapiens", "sample_count": 54},
}


def _assess(**kw):
    return assess_evidence(
        plan=kw.pop("plan", _PLAN),
        evidence=kw.pop("evidence", {}),
        claims=kw.pop("claims", []),
        **kw,
    )


class TestWhatIsEstablishedAndWhatIsMerelyHeld:
    def test_the_organisms_the_paper_states_verify_the_first_species_obligation(self):
        assessed = _assess()
        assert assessed["S1.A"]["outcome"] == VERIFIED
        assert "Homo sapiens" in assessed["S1.A"]["rationale"]
        assert assessed["S1.A"]["method"] == "measurement"

    def test_an_experiment_that_states_no_organism_leaves_it_undetermined_not_failed(self):
        """Section 3.4: bioAF's own limitation is never the paper's failure. An extraction that
        recorded no organism did not establish that the paper states none."""
        plan = {**_PLAN, "reported_experiments": [{**_EXPERIMENTS[0], "organism": None}]}
        assessed = _assess(plan=plan)
        assert assessed["S1.A"]["outcome"] == UNDETERMINED
        assert assessed["S1.A"]["next_action"]

    def test_a_measured_species_agreement_verifies_the_second_obligation(self):
        assessed = _assess(evidence={"precompute_checks": {"species_matches": {"verdict": "ok", "detail": "d"}}})
        assert assessed["S1.B"]["outcome"] == VERIFIED

    def test_a_measured_species_mismatch_fails_it_with_the_cause_recorded(self):
        assessed = _assess(
            evidence={
                "precompute_checks": {
                    "species_matches": {"verdict": "mismatch", "detail": "the deposit records Mus musculus"}
                }
            }
        )
        assert assessed["S1.B"]["outcome"] == FAILED
        assert "Mus musculus" in assessed["S1.B"]["rationale"]
        assert assessed["S1.B"]["impact"]

    def test_an_unknown_species_comparison_is_undetermined(self):
        assessed = _assess(evidence={"precompute_checks": {"species_matches": {"verdict": "unknown", "detail": "d"}}})
        assert assessed["S1.B"]["outcome"] == UNDETERMINED

    def test_a_models_holistic_methods_judgment_establishes_no_obligation(self):
        """Section 5: an LLM's "yes" without checked evidence does not establish any obligation. The
        precompute check is one model opinion about a whole methods section, and rubric v3 has no
        criterion that asks that question."""
        without = _assess()
        with_opinion = _assess(
            evidence={
                "precompute_checks": {
                    "methods_detailed_enough": {"verdict": "ok", "decided_by": "model", "detail": "it is fine"},
                    "samples_described_enough": {"verdict": "ok", "decided_by": "model", "detail": "also fine"},
                }
            }
        )
        assert with_opinion == without
        # And in particular it verifies no methods obligation on its own.
        assert all(with_opinion[leaf]["outcome"] != VERIFIED for leaf in ("M1.A", "M1.B", "M3.A", "M3.B", "M5.A"))

    def test_defined_arms_verify_the_groups_obligation(self):
        assert _assess()["S3.A"]["outcome"] == VERIFIED

    def test_a_contrast_missing_an_arm_leaves_the_groups_obligation_undetermined(self):
        plan = {**_PLAN, "differential_design": {"contrasts": [{**_CONTRASTS[0], "reference_condition": None}]}}
        assert _assess(plan=plan)["S3.A"]["outcome"] == UNDETERMINED

    def test_a_stated_sample_count_verifies_the_accounting_obligation(self):
        assert _assess()["S4.A"]["outcome"] == VERIFIED

    def test_a_reference_the_paper_stated_verifies_its_identification(self):
        assert _assess()["M2.A"]["outcome"] == VERIFIED
        assert "hg19" in _assess()["M2.A"]["rationale"]

    def test_an_assumed_annotation_leaves_the_version_obligation_undetermined(self):
        """bioAF substituting its own pinned release is exactly what "sufficiently specified to
        recover the intended reference inputs" is not."""
        plan = {
            **_PLAN,
            "reported_experiments": [
                {
                    **_EXPERIMENTS[0],
                    "reference": {
                        "assembly": {"stated": "hg19", "resolved": "GRCh37"},
                        "annotation": {"stated": None, "resolved": "Ensembl 87", "assumption": "bioAF's pinned release"},
                    },
                }
            ],
        }
        assessed = _assess(plan=plan)
        assert assessed["M2.A"]["outcome"] == VERIFIED
        assert assessed["M2.B"]["outcome"] == UNDETERMINED


class TestTheSamplesTheStudyActuallyHolds:
    def test_arms_every_record_agrees_with_verify_the_assignment_obligation(self):
        evidence = {
            "input_choice": {
                "mapping_validation": {
                    "status": "accepted",
                    "reasons": [],
                    "units_unresolved": [],
                    "mapping": [{"column": "c1", "arm": "test"}, {"column": "c2", "arm": "reference"}],
                }
            }
        }
        assessed = _assess(evidence=evidence)
        assert assessed["S3.B"]["outcome"] == VERIFIED
        assert assessed["S5.B"]["outcome"] == VERIFIED

    def test_a_mapping_held_on_unresolved_units_leaves_the_unit_obligation_undetermined(self):
        evidence = {
            "input_choice": {
                "mapping_validation": {
                    "status": "unresolved",
                    "reasons": ["nothing states which clone column KO1 came from"],
                    "units_unresolved": ["KO1", "KO2"],
                }
            }
        }
        assessed = _assess(evidence=evidence)
        assert assessed["S5.B"]["outcome"] == UNDETERMINED
        assert "KO1" in assessed["S5.B"]["next_action"]
        assert assessed["S3.B"]["outcome"] == UNDETERMINED

    def test_no_mapping_at_all_leaves_both_undetermined_and_never_failed(self):
        assessed = _assess()
        assert assessed["S3.B"]["outcome"] == UNDETERMINED
        assert assessed["S5.B"]["outcome"] == UNDETERMINED


class TestTheAuthorsResultsEarnTheirOwnPoints:
    _INVENTORY = {
        "findings": [
            {"id": "F1", "required": [10, 11], "importance": {"category": "primary"}},
        ]
    }

    def _claims(self, *outcomes):
        return [
            {"index": index, "consistency": {"outcome": outcome} if outcome else None}
            for index, outcome in outcomes
        ]

    def test_an_agreeing_comparison_verifies_its_allocated_result_leaf(self):
        assessed = _assess(claims=self._claims((10, "agree"), (11, "unresolved")), inventory=self._INVENTORY)
        assert assessed["R1.F1.10"]["outcome"] == VERIFIED
        assert assessed["R1.F1.11"]["outcome"] == UNDETERMINED

    def test_a_disagreeing_comparison_is_a_failed_result_check_and_says_so(self):
        assessed = _assess(claims=self._claims((10, "disagree"), (11, "agree")), inventory=self._INVENTORY)
        assert assessed["R1.F1.10"]["outcome"] == FAILED
        assert assessed["R1.F1.10"]["impact"]

    def test_a_not_checkable_comparison_earns_nothing_and_deducts_nothing(self):
        assessed = _assess(claims=self._claims((10, "not_checkable"), (11, None)), inventory=self._INVENTORY)
        assert assessed["R1.F1.10"]["outcome"] == UNDETERMINED

    def test_groffs_two_valid_checks_score_while_the_refinement_stays_grey(self):
        """Milestone A's acceptance for Groff: the 194 and 146 comparisons keep their credit while the
        88-gene interpretation is unresolved. Half the finding's R1 allocation, not all of it."""
        inventory = {"findings": [{"id": "F1", "required": [10, 11], "importance": {"category": "primary"}}]}
        assessed = _assess(claims=self._claims((10, "agree"), (11, "unresolved")), inventory=inventory)
        leaves = allocate(default_profile(), results=_result_allocation(inventory))
        card = score(leaves, assessed)
        assert card["sections"]["R"]["verified"] == Fraction(8, 2)
        assert card["sections"]["R"]["failed"] == 0
        assert card["verified"] > 0


class TestWhatIsNotImplementedIsDeclaredRatherThanGuessed:
    def test_every_unimplemented_obligation_is_named_with_why(self):
        """Section 10: unsupported obligations stay grey and are listed as capability limits. They must
        never read as completed implementations."""
        assert CAPABILITY_LIMITS
        for leaf_id, limit in CAPABILITY_LIMITS.items():
            assert limit["reason"], leaf_id
        assessed = _assess()
        for leaf_id in CAPABILITY_LIMITS:
            assert assessed.get(leaf_id, {}).get("outcome", UNDETERMINED) == UNDETERMINED, leaf_id

    def test_the_two_obligations_that_need_a_run_are_standing_capability_limits(self):
        """plan_8_4 milestone B: the static code obligations are implemented. Loading the source and
        resolving its dependencies execute it, so they stay behind the isolated execution path and its
        approval however much source is in hand."""
        assert "C1.B" in CAPABILITY_LIMITS and "C2.B" in CAPABILITY_LIMITS
        for implemented in ("C1.A", "C2.A", "C3.A", "C3.B", "C4.A", "C4.B", "C5.A", "C5.B"):
            assert implemented not in CAPABILITY_LIMITS

    def test_with_no_source_in_hand_the_whole_code_section_is_still_grey(self):
        assessed = _assess()
        for leaf in ("C1.A", "C1.B", "C2.A", "C2.B", "C3.A", "C3.B", "C4.A", "C4.B", "C5.A", "C5.B"):
            assert assessed[leaf]["outcome"] == UNDETERMINED, leaf
            assert assessed[leaf]["next_action"], leaf

    def test_a_repository_that_exists_verifies_nothing_about_the_code(self):
        """Section 3.4 and 6.2: repository discovery is not source inspection, and successful
        retrieval is not syntax validation."""
        evidence = {
            "capabilities": {
                "code_repository": {"value": "yes", "evidence": "github.com/example/paper"},
                "code_artifact": {"value": "yes", "evidence": "Supplemental File S2"},
            }
        }
        assessed = _assess(evidence=evidence)
        assert all(assessed.get(f"C{n}.{o}", {}).get("outcome", UNDETERMINED) == UNDETERMINED
                   for n in range(1, 6) for o in "AB")


def _result_allocation(inventory):
    from app.services.validation_rubric_v3 import result_allocation

    return result_allocation(inventory)


class TestTheCodeSectionIsAssessedWhereTheSourceIsInHand:
    """plan_8_4 milestone B: the code checks run on the source the study actually holds, and on nothing
    else. A repository that exists, a file that was retrieved and a role that says "code" are not
    source text, and none of them verifies a code obligation."""

    _SOURCE = {
        "path": "analysis.py",
        "language": "python",
        "text": "import os\n\n\ndef main():\n    print(os.getcwd())\n\n\nif __name__ == '__main__':\n    main()\n",
    }

    def test_source_in_hand_verifies_the_syntax_obligation_through_a_parser(self):
        assessed = _assess(evidence={"code_inspection": {"sources": [self._SOURCE]}})
        assert assessed["C1.A"]["outcome"] == VERIFIED
        assert "analysis.py" in assessed["C1.A"]["scope"]

    def test_the_execution_gated_obligations_stay_grey_without_an_approved_run(self):
        assessed = _assess(evidence={"code_inspection": {"sources": [self._SOURCE]}})
        assert assessed["C1.B"]["outcome"] == UNDETERMINED
        assert assessed["C2.B"]["outcome"] == UNDETERMINED

    def test_a_syntax_error_in_the_supplied_source_is_a_real_failure(self):
        broken = {**self._SOURCE, "text": "def main(:\n    pass\n"}
        assessed = _assess(evidence={"code_inspection": {"sources": [broken]}})
        assert assessed["C1.A"]["outcome"] == FAILED
        assert assessed["C1.A"]["impact"]

    def test_no_source_in_hand_leaves_the_whole_section_grey(self):
        assessed = _assess(evidence={"capabilities": {"code_repository": {"value": "yes"}}})
        assert all(assessed[f"C{n}.{o}"]["outcome"] == UNDETERMINED for n in range(1, 6) for o in "AB")

    def test_an_unparseable_language_declares_itself_a_capability_limit(self):
        r_source = {"path": "AllRCode.R", "language": "r", "text": "f <- function(x) x + 1\n"}
        assessed = _assess(evidence={"code_inspection": {"sources": [r_source]}})
        assert assessed["C1.A"]["outcome"] == UNDETERMINED
        assert assessed["C1.A"]["capability_limit"] is True


class TestApplicabilityIsAboutThePaperNotAboutBioaf:
    """plan_8_4 section 3.5: a criterion is excluded only where cited evidence establishes that the
    paper's methods have no counterpart for it. A missing adapter, absent code, controlled samples and
    an unsupported assay are never grounds, and an uncertain exclusion is not made."""

    def _profile(self, assays):
        from app.services.validation_rubric_evidence import profile_for

        return profile_for(
            plan={
                "reported_experiments": [
                    {"id": f"e{i}", "assay": assay, "reference": {}} for i, assay in enumerate(assays, start=1)
                ]
            }
        )

    def test_a_paper_with_no_genomic_analysis_excludes_the_reference_criterion(self):
        profile = self._profile(["western blotting", "immunofluorescence microscopy", "qRT-PCR"])
        assert "M2" not in profile["weights"]
        (excluded,) = profile["exclusions"]
        assert excluded["criterion"] == "M2"
        assert "western blotting" in excluded["rationale"]
        assert excluded["source"]

    def test_the_excluded_weight_is_redistributed_and_the_profile_still_totals_one_hundred(self):
        from fractions import Fraction

        from app.services.validation_rubric_v3 import allocate

        profile = self._profile(["western blotting", "qRT-PCR"])
        assert sum((leaf["weight"] for leaf in allocate(profile)), Fraction(0)) == 100
        assert sum(profile["sections"].values()) == 100

    def test_one_genomic_experiment_is_enough_to_keep_the_criterion(self):
        profile = self._profile(["bulk RNA-seq", "western blotting"])
        assert "M2" in profile["weights"]
        assert profile["exclusions"] == []

    def test_an_assay_bioaf_cannot_execute_is_never_a_ground_for_exclusion(self):
        """A missing adapter says what bioAF cannot run. It says nothing about whether the paper's
        methods have a reference to state."""
        profile = self._profile(["ribosome profiling"])
        assert "M2" in profile["weights"]
        assert profile["exclusions"] == []

    def test_a_paper_whose_assays_are_unknown_excludes_nothing(self):
        profile = self._profile([None, ""])
        assert "M2" in profile["weights"]
        assert profile["exclusions"] == []
