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
        """plan_8_5 section 3.5 gave bioAF an R parser. Julia is a language it still cannot read, and
        a language with no parser leaves its obligation grey and declares why."""
        source = {"path": "analysis.jl", "language": "julia", "text": "f(x) = x + 1\n"}
        assessed = _assess(evidence={"code_inspection": {"sources": [source]}})
        assert assessed["C1.A"]["outcome"] == UNDETERMINED
        assert assessed["C1.A"]["capability_limit"] is True

    def test_the_r_a_paper_supplied_is_parsed_rather_than_declared_unreadable(self):
        source = {"path": "AllRCode.R", "language": "r", "text": "library(DESeq2)\nprint(1)\n"}
        assessed = _assess(evidence={"code_inspection": {"sources": [source]}})
        assert assessed["C1.A"]["outcome"] == VERIFIED


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


class TestThresholdCreditRestsOnTheStatedCriteria:
    """plan_8_5 section 3.3: a nonempty dictionary is not a threshold.

    M4.A asks whether the definitions the paper's analysis actually uses are stated: raw versus
    adjusted significance, the correction where one is used, and the effect threshold on its own
    scale. The extraction's ``thresholds`` pair exists on every contrast and holds ``None`` where
    nothing was stated, so its presence establishes nothing at all.
    """

    def _plan(self, contrast: dict) -> dict:
        return {**_PLAN, "differential_design": {"contrasts": [contrast]}}

    def test_a_threshold_pair_of_nulls_establishes_nothing(self):
        contrast = {k: v for k, v in _CONTRASTS[0].items() if k != "cutoffs"}
        assessed = _assess(plan=self._plan({**contrast, "thresholds": {"padj": None, "log2fc": None}}))
        assert assessed["M4.A"]["outcome"] == UNDETERMINED
        assert assessed["M4.A"]["next_action"]

    def test_a_stated_significance_cutoff_verifies_it_and_names_the_definition(self):
        assessed = _assess()
        assert assessed["M4.A"]["outcome"] == VERIFIED
        assert "0.05" in assessed["M4.A"]["rationale"]

    def test_an_effect_threshold_of_zero_is_a_stated_threshold(self):
        """A legitimate zero is not an absent one: "any positive fold change" is a threshold."""
        contrast = {
            **_CONTRASTS[0],
            "cutoffs": [
                {"kind": "padj", "operator": "<", "value": 0.05},
                {"kind": "abs_log2fc", "operator": ">", "value": 0},
            ],
        }
        assessed = _assess(plan=self._plan(contrast))
        assert assessed["M4.A"]["outcome"] == VERIFIED

    def test_an_analysis_that_applies_no_fold_change_requirement_is_not_missing_one(self):
        """Section 3.3: do not require every possible threshold for an analysis that does not use it."""
        contrast = {k: v for k, v in _CONTRASTS[0].items() if k != "cutoffs"}
        assessed = _assess(plan=self._plan({**contrast, "thresholds": {"padj": 0.05, "log2fc": None}}))
        assert assessed["M4.A"]["outcome"] == VERIFIED

    def test_a_threshold_the_paper_never_stated_for_this_contrast_stays_untested(self):
        """The paper-level pair says nothing either way about this contrast's fold change."""
        contrast = {k: v for k, v in _CONTRASTS[0].items() if k != "cutoffs"}
        plan = {**self._plan(contrast), "differential_design": {"contrasts": [contrast], "thresholds": {"padj": 0.05}}}
        assessed = _assess(plan=plan)
        assert assessed["M4.A"]["outcome"] == UNDETERMINED

    def test_one_contrast_without_a_stated_threshold_holds_the_obligation_open(self):
        bare = {k: v for k, v in _CONTRASTS[0].items() if k != "cutoffs"}
        plan = {**_PLAN, "differential_design": {"contrasts": [_CONTRASTS[0], {**bare, "name": "second"}]}}
        assessed = _assess(plan=plan)
        assert assessed["M4.A"]["outcome"] == UNDETERMINED
        assert "second" in assessed["M4.A"]["rationale"]


class TestTheReadingOfAThresholdMustBeEstablishedNotAssumed:
    """plan_8_5 section 3.3: the absence of a field in a projection is not proof of no ambiguity.

    M4.B is about direction, scale, contrast orientation and interpretation. It is established from
    the normalized predicate bioAF built for each claim, and claims carrying no predicate at all
    establish nothing: that is exactly the state the old check read as unambiguous.
    """

    _RESOLVED = {
        "status": "resolved",
        "direction": "down",
        "orientation": "test_over_reference",
        "significance": {"kind": "padj", "operator": "<", "value": 0.05, "adjustment": "BH"},
        "effect": {"kind": "none"},
    }

    def test_claims_carrying_no_predicate_leave_the_reading_untested(self):
        assessed = _assess(claims=[{"index": 0}, {"index": 1}])
        assert assessed["M4.B"]["outcome"] == UNDETERMINED
        assert assessed["M4.B"]["next_action"]

    def test_a_resolved_predicate_verifies_the_reading_and_says_what_it_reads(self):
        assessed = _assess(claims=[{"index": 0, "predicate_detail": self._RESOLVED}])
        assert assessed["M4.B"]["outcome"] == VERIFIED
        assert "down" in assessed["M4.B"]["rationale"]

    def test_a_predicate_that_could_not_be_read_leaves_it_untested_with_the_reason(self):
        detail = {**self._RESOLVED, "status": "not_checkable", "reason": "the claim states no significance cutoff"}
        assessed = _assess(claims=[{"index": 0, "predicate_detail": detail}])
        assert assessed["M4.B"]["outcome"] == UNDETERMINED
        assert "significance cutoff" in assessed["M4.B"]["rationale"]

    def test_an_unresolved_signed_or_absolute_filter_still_holds_it_open(self):
        claims = [
            {
                "index": 0,
                "predicate_detail": self._RESOLVED,
                "consistency": {"filter_semantics": {"unresolved": True}},
            }
        ]
        assessed = _assess(claims=claims)
        assert assessed["M4.B"]["outcome"] == UNDETERMINED

    def test_a_stated_threshold_alone_never_proves_the_reading(self):
        """Section 3.3: a numerical threshold alone does not prove B."""
        assessed = _assess(claims=[{"index": 0, "consistency": {"outcome": "agree"}}])
        assert assessed["M4.A"]["outcome"] == VERIFIED
        assert assessed["M4.B"]["outcome"] == UNDETERMINED


class TestReferenceRecoverabilityIsAboutThePaperNotAboutBioaf:
    """plan_8_5 section 3.3: M2.B is the recoverability of the paper's reference versions.

    A release bioAF cannot supply is still a release the paper specified, and bioAF choosing a
    default in its place is a fact about bioAF's run, not about the paper's reporting.
    """

    def _plan(self, reference: dict) -> dict:
        return {**_PLAN, "reported_experiments": [{**_EXPERIMENTS[0], "reference": reference}]}

    def test_a_release_bioaf_cannot_supply_is_still_specified_by_the_paper(self):
        reference = {
            "assembly": {"stated": "hg19", "resolved": "GRCh37", "status": "usable"},
            "annotation": {
                "stated": "GENCODE v19",
                "status": "unavailable",
                "reason": "bioAF supplies GENCODE v44 for GRCh37, and never swaps one release for another",
            },
        }
        assessed = _assess(plan=self._plan(reference))
        assert assessed["M2.B"]["outcome"] == VERIFIED
        assert "GENCODE v19" in assessed["M2.B"]["rationale"]

    def test_an_annotation_the_read_never_reached_is_untested_and_does_not_blame_the_paper(self):
        reference = {
            "assembly": {"stated": "hg19", "resolved": "GRCh37", "status": "usable"},
            "annotation": {"stated": None, "status": "not_read", "reason": "the paper's annotation was not read"},
        }
        assessed = _assess(plan=self._plan(reference))
        assert assessed["M2.B"]["outcome"] == UNDETERMINED
        assert "not read" in assessed["M2.B"]["rationale"]
        assert "does not state" not in assessed["M2.B"]["rationale"]

    def test_two_statements_naming_different_assemblies_fail_with_their_cause(self):
        reference = {
            "assembly": {
                "stated": "hg19",
                "status": "unresolved",
                "conflict": True,
                "reason": "the paper states GRCh37 and an annotation release that belongs to GRCh38",
            },
            "annotation": {"stated": "GENCODE v44", "status": "unresolved", "conflict": True},
        }
        assessed = _assess(plan=self._plan(reference))
        assert assessed["M2.B"]["outcome"] == FAILED
        assert assessed["M2.B"]["impact"]


def _records(*organisms, accession="GSE1", limitations=None):
    return {
        "sample_records": {
            "deposits": (
                [
                    {
                        "accession": accession,
                        "source": f"https://example/{accession}",
                        "sample_count": len(organisms),
                        "samples": [
                            {"accession": f"GSM{i}", "organism": organism, "characteristics": {}}
                            for i, organism in enumerate(organisms, start=1)
                        ],
                    }
                ]
                if organisms
                else []
            ),
            "limitations": limitations or [],
        }
    }


class TestSpeciesAgreementIsMeasuredAgainstTheRecordsThemselves:
    """plan_8_5 section 3.4: S1.B compares the paper with the deposit's own sample records.

    Not with a flag left by an earlier check, and never with an organism field missing from bioAF's
    manifest: what bioAF failed to retrieve is bioAF's limitation, and saying "the deposit declares
    no organism" about a deposit nobody opened is a statement bioAF has no evidence for.
    """

    def test_records_declaring_the_organism_the_paper_states_verify_it(self):
        assessed = _assess(evidence=_records("Homo sapiens", "Homo sapiens"))
        assert assessed["S1.B"]["outcome"] == VERIFIED
        assert "GSE1" in assessed["S1.B"]["scope"]

    def test_records_declaring_a_different_organism_fail_it_and_name_the_samples(self):
        assessed = _assess(evidence=_records("Mus musculus", "Mus musculus"))
        assert assessed["S1.B"]["outcome"] == FAILED
        assert "Mus musculus" in assessed["S1.B"]["rationale"]
        assert assessed["S1.B"]["impact"]

    def test_a_deposit_holding_two_species_still_agrees_where_it_holds_the_papers(self):
        """A xenograft or a spike-in deposits two organisms, and the paper names the one it analysed."""
        assessed = _assess(evidence=_records("Homo sapiens", "Mus musculus"))
        assert assessed["S1.B"]["outcome"] == VERIFIED

    def test_a_deposit_bioaf_never_opened_leaves_it_untested_and_names_the_limitation(self):
        limitation = {"accession": "EGAS1", "reason": "bioAF reads per-sample records from GEO only"}
        assessed = _assess(evidence=_records(limitations=[limitation]))
        assert assessed["S1.B"]["outcome"] == UNDETERMINED
        assert "GEO only" in assessed["S1.B"]["rationale"]

    def test_records_that_were_read_and_state_no_organism_say_that_about_the_deposit(self):
        assessed = _assess(evidence=_records("", ""))
        assert assessed["S1.B"]["outcome"] == UNDETERMINED
        assert "states no organism" in assessed["S1.B"]["rationale"]

    def test_the_records_are_preferred_over_an_older_checks_verdict(self):
        evidence = {
            **_records("Mus musculus"),
            "precompute_checks": {"species_matches": {"verdict": "ok", "detail": "an older run agreed"}},
        }
        assert _assess(evidence=evidence)["S1.B"]["outcome"] == FAILED


def _sample(accession, **fields):
    return {
        "accession": accession,
        "title": fields.pop("title", accession),
        "organism": fields.pop("organism", "Homo sapiens"),
        "source_name": fields.pop("source_name", ""),
        "molecule": fields.pop("molecule", ""),
        "library_strategy": fields.pop("library_strategy", ""),
        "characteristics": fields.pop("characteristics", {}),
        "unkeyed": fields.pop("unkeyed", []),
    }


def _deposit(*samples, accession="GSE1"):
    return {
        "sample_records": {
            "deposits": [
                {
                    "accession": accession,
                    "source": f"https://example/{accession}",
                    "sample_count": len(samples),
                    "samples": list(samples),
                }
            ],
            "limitations": [],
        }
    }


class TestTheMaterialTheSamplesCameFrom:
    """plan_8_5 section 3.4: S2, from the paper and from the deposit's own records.

    Species, material, group assignment, counts and unit identity are decoupled: each one is
    established by its own evidence, and an unresolved one does not hold the others open.
    """

    _STATED = {
        "paper_statements": {
            "sample_material": {
                "value": "HepG2 cells",
                "quote": "RNA was prepared from HepG2 cells",
                "method": "model_assisted",
            }
        }
    }

    def test_the_material_the_paper_states_verifies_the_first_obligation(self):
        assessed = _assess(evidence=self._STATED)
        assert assessed["S2.A"]["outcome"] == VERIFIED
        assert "HepG2" in assessed["S2.A"]["rationale"]
        assert assessed["S2.A"]["method"] == "model_assisted"

    def test_a_paper_that_names_no_material_leaves_it_untested_not_failed(self):
        assert _assess()["S2.A"]["outcome"] == UNDETERMINED
        assert _assess()["S2.A"]["next_action"]

    def test_records_that_name_the_same_material_verify_the_agreement(self):
        evidence = {
            **self._STATED,
            **_deposit(_sample("GSM1", source_name="HepG2 cells"), _sample("GSM2", source_name="HepG2 cells")),
        }
        assessed = _assess(evidence=evidence)
        assert assessed["S2.B"]["outcome"] == VERIFIED

    def test_records_that_name_a_different_material_leave_it_open_rather_than_failing_it(self):
        """A paper's experiments legitimately differ, so a disagreement here is a question, not a
        contradiction: what fails S2.B is beyond what a name comparison establishes."""
        evidence = {**self._STATED, **_deposit(_sample("GSM1", source_name="primary fibroblast"))}
        assessed = _assess(evidence=evidence)
        assert assessed["S2.B"]["outcome"] == UNDETERMINED
        assert "fibroblast" in assessed["S2.B"]["rationale"]

    def test_records_with_no_material_at_all_say_that_about_the_deposit(self):
        assessed = _assess(evidence={**self._STATED, **_deposit(_sample("GSM1"))})
        assert assessed["S2.B"]["outcome"] == UNDETERMINED


class TestTheSamplesAreCountedAgainstTheRecords:
    """S4.B: the held records reconciled to the counts the paper states, per experiment."""

    def test_records_that_match_the_stated_count_verify_it(self):
        evidence = _deposit(*[_sample(f"GSM{i}") for i in range(1, 55)])
        assessed = _assess(evidence=evidence)
        assert assessed["S4.B"]["outcome"] == VERIFIED
        assert "54" in assessed["S4.B"]["rationale"]

    def test_records_that_contradict_the_stated_count_fail_it_with_both_numbers(self):
        evidence = _deposit(*[_sample(f"GSM{i}") for i in range(1, 9)])
        assessed = _assess(evidence=evidence)
        assert assessed["S4.B"]["outcome"] == FAILED
        assert "8" in assessed["S4.B"]["rationale"] and "54" in assessed["S4.B"]["rationale"]
        assert assessed["S4.B"]["impact"]

    def test_a_series_wide_count_is_not_substituted_for_an_experiments_count(self):
        """Section 3.2: a whole-series count is not an experiment's count. Where the paper states a
        per-experiment count and the deposit holds a whole series, the two are not compared."""
        plan = {
            **_PLAN,
            "reported_experiments": [
                {**_EXPERIMENTS[0], "sample_count": 12},
                {**_EXPERIMENTS[0], "id": "e2", "sample_count": 42},
            ],
        }
        assessed = _assess(plan=plan, evidence=_deposit(*[_sample(f"GSM{i}") for i in range(1, 55)]))
        assert assessed["S4.B"]["outcome"] == UNDETERMINED
        assert assessed["S4.B"]["next_action"]

    def test_no_records_leaves_it_untested(self):
        assert _assess()["S4.B"]["outcome"] == UNDETERMINED


class TestReplicationIsDescribedBeforeItIsChecked:
    """S5.A: what the paper says about biological versus technical replication and pairing."""

    def test_a_stated_replication_design_verifies_it(self):
        evidence = {
            "paper_statements": {
                "replication": {"value": "three biological replicates per condition", "quote": "n = 3 embryos"}
            }
        }
        assessed = _assess(evidence=evidence)
        assert assessed["S5.A"]["outcome"] == VERIFIED
        assert "biological" in assessed["S5.A"]["rationale"]

    def test_records_that_key_a_replicate_establish_it_where_the_paper_did_not(self):
        evidence = _deposit(
            _sample("GSM1", characteristics={"replicate": "1"}),
            _sample("GSM2", characteristics={"replicate": "2"}),
        )
        assessed = _assess(evidence=evidence)
        assert assessed["S5.A"]["outcome"] == VERIFIED

    def test_nothing_about_replication_leaves_it_untested(self):
        assert _assess()["S5.A"]["outcome"] == UNDETERMINED

    def test_a_sample_name_pattern_is_never_read_as_a_donor(self):
        """Section 3.4: do not infer donor identity from a sample-name pattern."""
        evidence = _deposit(_sample("GSM1", title="Donor3_rep1"), _sample("GSM2", title="Donor3_rep2"))
        assert _assess(evidence=evidence)["S5.A"]["outcome"] == UNDETERMINED


class TestGroupAssignmentIsNotHeldByTheComputeInput:
    """plan_8_5 section 3.4: a documentary check does not wait for an analysis input to be chosen."""

    def test_records_that_carry_the_papers_arms_support_the_assignment(self):
        evidence = _deposit(
            _sample("GSM1", characteristics={"condition": "XX whole embryos"}),
            _sample("GSM2", characteristics={"condition": "XY whole embryos"}),
        )
        assessed = _assess(evidence=evidence)
        assert assessed["S3.B"]["outcome"] == VERIFIED
        assert "GSE1" in assessed["S3.B"]["scope"]

    def test_records_that_carry_none_of_them_leave_it_open(self):
        evidence = _deposit(_sample("GSM1", characteristics={"condition": "something else"}))
        assert _assess(evidence=evidence)["S3.B"]["outcome"] == UNDETERMINED

    def test_an_accepted_mapping_still_establishes_it(self):
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
        assert _assess(evidence=evidence)["S3.B"]["outcome"] == VERIFIED


class TestAJudgmentSettlesWhatNoMeasurementCould:
    """plan_8_5 section 3.6: the accepted documentary judgments reach the obligations they answer.

    A measurement outranks a judgment: where bioAF compared something and got an answer, a model's
    opinion about the same obligation does not overwrite it.
    """

    def _judged(self, leaf, outcome, **extra):
        return {
            "rubric_judgments": {
                "judgments": {
                    leaf: {
                        "outcome": outcome,
                        "rationale": "the assessor judged it",
                        "scope": "2 supplied passages",
                        "method": "model_assisted",
                        "evidence": {"citations": ["m1"]},
                        "assessor": {"model": "m", "contract_version": 1},
                        **extra,
                    }
                }
            }
        }

    def test_an_accepted_judgment_verifies_its_obligation(self):
        assessed = _assess(evidence=self._judged("E1.A", VERIFIED))
        assert assessed["E1.A"]["outcome"] == VERIFIED
        assert assessed["E1.A"]["method"] == "model_assisted"
        assert assessed["E1.A"]["evidence"]["citations"] == ["m1"]

    def test_an_obligation_with_no_judgment_says_what_would_settle_it(self):
        assessed = _assess()
        assert assessed["M3.A"]["outcome"] == UNDETERMINED
        assert assessed["M3.A"]["next_action"]
        assert not assessed["M3.A"].get("capability_limit"), "bioAF implements this check now"

    def test_a_judgment_never_overwrites_a_measurement_that_settled(self):
        evidence = {
            **_records("Mus musculus"),
            "rubric_judgments": {
                "judgments": {
                    "S1.B": {"outcome": VERIFIED, "rationale": "the assessor thought it agreed", "method": "model_assisted"}
                }
            },
        }
        assert _assess(evidence=evidence)["S1.B"]["outcome"] == FAILED

    def test_a_judged_failure_carries_its_impact(self):
        assessed = _assess(evidence=self._judged("E2.B", FAILED, impact="the comparison has no control"))
        assert assessed["E2.B"]["outcome"] == FAILED
        assert assessed["E2.B"]["impact"]

    def test_the_experimental_obligations_are_no_longer_capability_limits(self):
        for leaf in ("E1.A", "E1.B", "E2.A", "E2.B", "E3.A", "E3.B", "M1.A", "M1.B", "M3.A", "M3.B", "M5.A", "M5.B"):
            assert leaf not in CAPABILITY_LIMITS, leaf


class TestWhatCompletionOfTheDocumentaryRubricMeans:
    """plan_8_5 gate 3: every C, S, E and M obligation has an implemented assessor.

    The gate is a list, not a judgment call: the only obligations that may remain declared capability
    limits are the ones that cannot be settled by reading anything, because they need an approved run.
    A narrower release than that is unfinished work, however correct its grey presentation.
    """

    _NEEDS_A_RUN = {"C1.B", "C2.B", "R2", "R3"}

    def test_only_the_obligations_that_need_a_run_remain_capability_limits(self):
        assert set(CAPABILITY_LIMITS) == self._NEEDS_A_RUN

    def test_every_documentary_obligation_has_an_assessor(self):
        from app.services.validation_rubric_v3 import CRITERIA

        documentary = {
            f"{criterion.id}.{obligation}"
            for criterion in CRITERIA
            if criterion.section in ("C", "S", "E", "M")
            for obligation in ("A", "B")
        }
        assert not (documentary & set(CAPABILITY_LIMITS)) - self._NEEDS_A_RUN

    def test_the_two_that_need_a_run_have_a_route_a_person_can_approve(self):
        """A standing limit is only honest where the way to settle it exists."""
        from app.services.validation_environment_check import environment_check_request

        request = environment_check_request(
            sources=[{"path": "a.R", "language": "r", "text": "library(DESeq2)\n"}], manifests=[]
        )
        assert set(request["establishes"]) == {"C1.B", "C2.B"}
        assert request["approval"]["required"] is True
