"""change_7.1 section 7: the outcome and its reasons come from what the evidence says.

Study 32 was refused the DEPOSIT route because the EGA deposit holds no processed matrix, and was
then classified `access_restricted`. Controlled access is what blocks the PIPELINE route; this
route was blocked by a missing input. The stored reason went further and said "No pre-processed
data to reproduce the finding from is published for this paper", a claim about the paper that the
same run contradicted by discovering a 194-row results table.

Section 7 asks for the distinctions to survive: controlled access, failed discovery, unsupported
acquisition, missing inputs, and attempted execution failure are different outcomes, more than one
can be true at once, and every limitation has to name what it actually affects.
"""

import pytest

from app.services.validation_completion import completion_for

_EGA_DEPOSIT = {
    "archive": "ega",
    "accession": "EGAS00001003667",
    "scoped": True,
    "exists": "yes",
    "access": "controlled",
    "supported": "no",
    "raw_data": "yes",
    "preprocessed_data": "no",
}
_RESULTS_TABLE = {
    "label": "Supplemental File S3",
    "role": "results_table",
    "resolved": True,
    "row_count": 194,
}


class TestTheOutcomeMatchesTheObstacle:
    def test_a_missing_matrix_is_not_controlled_access(self):
        """The deposit route needs a processed matrix. The deposit published none. That is a missing
        input, and calling it access_restricted describes the wrong obstacle.

        change_7.3 section 5 (flagged test change): this used the EGA deposit, and for an archive
        bioAF has no adapter for the refusal is now the missing adapter, with the listing carried
        beside it. A missing input needs a deposit bioAF can read, so the promise is held on one.
        """
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[_RESULTS_TABLE],
        )
        assert outcome["classification"] == "missing_data"

    def test_controlled_raw_data_blocking_the_pipeline_is_access_restricted(self):
        outcome = completion_for(
            route="pipeline",
            capabilities={"deposits": [_EGA_DEPOSIT], "raw_data": {"value": "yes"}},
            supplements=[_RESULTS_TABLE],
        )
        assert outcome["classification"] == "access_restricted"

    def test_a_discovery_failure_is_neither(self):
        """Nothing was established, so nothing about the paper may be concluded."""
        outcome = completion_for(
            route="pipeline",
            capabilities={
                "deposits": [],
                "raw_data": {"value": "unknown", "failure_reason": "bioAF could not reach ENA"},
            },
            supplements=[],
        )
        assert outcome["classification"] == "inconclusive"


class TestEveryLimitationNamesWhatItAffects:
    def test_the_reason_is_scoped_to_the_deposit_not_the_paper(self):
        """'is published for this paper' was contradicted by the paper's own results table."""
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_RESULTS_TABLE],
        )
        assert "EGAS00001003667" in outcome["reason"]
        assert "for this paper" not in outcome["reason"]

    def test_a_discovered_results_table_forbids_a_blanket_no_processed_results(self):
        """The acceptance line: a discovered results table cannot yield a blanket
        no-processed-results conclusion."""
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_RESULTS_TABLE],
        )
        assert "Supplemental File S3" in outcome["reason"]

    def test_processed_results_and_a_reproduction_input_are_reported_separately(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_RESULTS_TABLE],
        )
        # change_7.3 section 4 (flagged test change): both facts are tri-state now.
        assert outcome["processed_results_available"] == "yes"
        # change_7.4 section 1.3 (flagged test change): the input fact reads the acquisition record.
        assert outcome["input_acquired"] == "no"

    def test_more_than_one_limitation_survives(self):
        """Section 7: preserve multiple simultaneous limitations rather than forcing every
        observation into one explanation.

        change_7.2 section 1 changed which limitation the EGA leg produces. Both the adapter gap and
        the authorization gap are true of a controlled EGA dataset, and the adapter is asked first
        because it is the axis bioAF owns: telling a lab to negotiate data access for a capability
        gap sends it to the wrong remedy.
        """
        outcome = completion_for(
            route="both",
            capabilities={
                "deposits": [_EGA_DEPOSIT],
                "raw_data": {"value": "yes"},
                "preprocessed_data": {"value": "no"},
            },
            supplements=[_RESULTS_TABLE],
        )
        # change_7.3 section 5 (flagged test change): the deposit leg asks the adapter question first
        # too, so both legs of a controlled EGA deposit are the adapter gap. Each leg keeps its own.
        assert {limitation["operation"] for limitation in outcome["limitations"]} == {"deposit", "pipeline"}
        assert {limitation["kind"] for limitation in outcome["limitations"]} == {"unsupported_acquisition"}

    def test_controlled_access_is_still_reached_where_the_adapter_exists(self):
        """The authorization axis is only reachable once bioAF can read the archive at all. Section 9
        turns the EGA deposit above into exactly this shape."""
        outcome = completion_for(
            route="pipeline",
            capabilities={
                "deposits": [
                    {
                        "archive": "geo",
                        "accession": "GSE000001",
                        "access": "controlled",
                        "supported": "yes",
                        "raw_data": "yes",
                    }
                ],
                "raw_data": {"value": "yes"},
            },
            supplements=[],
        )
        assert {limitation["kind"] for limitation in outcome["limitations"]} == {"controlled_access"}
        assert "not authorised" in outcome["reason"]

    def test_each_limitation_names_its_resource(self):
        outcome = completion_for(
            route="both",
            capabilities={
                "deposits": [_EGA_DEPOSIT],
                "raw_data": {"value": "yes"},
                "preprocessed_data": {"value": "no"},
            },
            supplements=[_RESULTS_TABLE],
        )
        assert all(limitation.get("resource") for limitation in outcome["limitations"])


class TestTheChecksThatRanAreStated:
    def test_a_completed_assessment_says_what_it_checked(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_RESULTS_TABLE, {"label": "Supplemental File S2", "role": "code", "resolved": True}],
        )
        assert "Supplemental File S2" in " ".join(outcome["checks_completed"])

    def test_an_unretrieved_supplement_is_named_as_not_checked(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[{"label": "Supplemental File S9", "role": "unknown", "resolved": False}],
        )
        assert "Supplemental File S9" in " ".join(outcome["checks_not_completed"])

    def test_result_table_consistency_is_not_called_reproduction(self):
        """Section 7: result-table consistency and independent reproduction stay distinct."""
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_RESULTS_TABLE],
        )
        joined = " ".join(outcome["checks_completed"]).lower()
        assert "consistency" in joined
        assert "reproduc" not in joined


class TestARetrievedFileWithNoEstablishedRoleIsReported:
    """change_7.3 section 3: `checks_completed` dropped resolved rows whose role was unknown, so a
    retrieved document the classifier could not place disappeared from the report."""

    def test_it_is_listed_as_retrieved_with_no_role(self):
        from app.services.validation_completion import completion_for

        outcome = completion_for(
            route="deposit",
            capabilities={"preprocessed_data": {"value": "no"}, "deposits": []},
            supplements=[
                {
                    "label": "Supplemental Material (materials.docx)",
                    "filename": "materials.docx",
                    "kind": "attachment",
                    "resolved": True,
                    "role": "unknown",
                }
            ],
        )
        assert any("retrieved; role not established" in row for row in outcome["checks_completed"])

    def test_figures_and_index_pages_are_not_listed_as_checks(self):
        from app.services.validation_completion import completion_for

        outcome = completion_for(
            route="deposit",
            capabilities={"preprocessed_data": {"value": "no"}, "deposits": []},
            supplements=[
                {"label": "f1.jpg", "filename": "f1.jpg", "kind": "figure", "resolved": True, "role": "unknown"},
                {"label": "index.html", "filename": "index.html", "kind": "index", "resolved": True, "role": "unknown"},
            ],
        )
        assert outcome["checks_completed"] == []


# ---- change_7.3 sections 4 and 5 --------------------------------------------------------------------

_GEO_NO_MATRIX = {
    "archive": "geo",
    "accession": "GSE123456",
    "scoped": True,
    "exists": "yes",
    "access": "public",
    "supported": "yes",
    "raw_data": "yes",
    "preprocessed_data": "no",
}


def _attachment(label, *, resolved, role="unknown", status=None, ledger="R1"):
    return {
        "label": label,
        "filename": f"{label.replace(' ', '_')}.txt",
        "kind": "attachment",
        "resolved": resolved,
        "role": role,
        "retrieval": {"status": status or ("retrieved" if resolved else "failed"), "ledger": ledger},
    }


class TestTheCompletionFactsAreTriState:
    """A failed download always yielded "Processed results published: No"."""

    def test_an_uninspected_attachment_leaves_processed_results_not_established(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental File S3", resolved=False)],
        )
        assert outcome["processed_results_available"] == "not_established"
        assert outcome["processed_results_reason"]

    def test_an_inspected_results_table_is_yes(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental File S3", resolved=True, role="results_table")],
        )
        assert outcome["processed_results_available"] == "yes"

    def test_no_requires_every_attachment_inspected(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental File S1", resolved=True, role="sample_metadata")],
        )
        assert outcome["processed_results_available"] == "no"

    def test_reproduction_input_is_not_established_while_an_attachment_is_uninspected(self):
        """change_7.4 section 1.3 (flagged test change): acquisition is what bioAF did, so it is "no";
        whether an uninspected attachment is usable is the part that is not established."""
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental File S1", resolved=False)],
        )
        assert outcome["input_acquired"] == "no"
        assert "not inspected" in outcome["input_acquired_reason"]
        assert outcome["input_usable"] == "not_established"

    def test_reproduction_input_is_yes_for_a_retrieved_matrix(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental Table S2", resolved=True, role="expression_matrix")],
        )
        # change_7.4 section 1.3 (flagged test change): the same fact, read from the acquisition record.
        assert outcome["input_acquired"] == "yes"

    def test_a_paper_whose_attachments_were_never_listed_is_not_established(self):
        """A pasted body carries no manifest: nobody looked, which is not "the paper attached none"."""
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[],
            manifest_known=False,
        )
        assert outcome["processed_results_available"] == "not_established"


class TestAnUnsupportedArchiveIsNoAdapterOnEveryLeg:
    """The adapter question comes before the input question. `no_input` means the adapter exists
    and the resource holds nothing that could serve; for EGA the adapter does not exist."""

    def test_an_ega_deposit_holding_only_raw_reads_is_unsupported_on_the_deposit_leg(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
            supplements=[_RESULTS_TABLE],
        )
        kinds = {limitation["kind"] for limitation in outcome["limitations"]}
        assert kinds == {"unsupported_acquisition"}
        assert outcome["classification"] == "access_restricted"

    def test_the_listing_fact_is_carried_beside_it(self):
        deposit = {
            **_EGA_DEPOSIT,
            "evidence_by_key": {"preprocessed_data": "EGA lists 108 file(s), none of them processed tables"},
        }
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [deposit], "preprocessed_data": {"value": "no"}},
            supplements=[],
        )
        limitation = outcome["limitations"][0]
        assert limitation["observation"] == "EGA lists 108 file(s), none of them processed tables"

    def test_an_unsupported_archive_listing_processed_files_is_also_no_adapter(self):
        deposit = {**_EGA_DEPOSIT, "preprocessed_data": "yes"}
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [deposit], "preprocessed_data": {"value": "yes"}},
            supplements=[],
        )
        assert {limitation["kind"] for limitation in outcome["limitations"]} == {"unsupported_acquisition"}


class TestEveryLegIsReported:
    def test_a_deposit_route_study_still_states_what_the_raw_read_route_faces(self):
        outcome = completion_for(
            route="deposit",
            capabilities={
                "deposits": [_EGA_DEPOSIT],
                "raw_data": {"value": "yes"},
                "preprocessed_data": {"value": "no"},
            },
            supplements=[],
        )
        context = outcome["other_legs"]
        assert [c["operation"] for c in context] == ["pipeline"]
        assert context[0]["kind"] == "unsupported_acquisition"

    def test_the_other_legs_never_decide_the_classification(self):
        outcome = completion_for(
            route="deposit",
            capabilities={
                "deposits": [_GEO_NO_MATRIX, _EGA_DEPOSIT],
                "raw_data": {"value": "yes"},
                "preprocessed_data": {"value": "no"},
            },
            supplements=[_attachment("Supplemental File S1", resolved=True, role="sample_metadata")],
        )
        assert all(limitation["operation"] == "deposit" for limitation in outcome["limitations"])


class TestMissingDataNeedsAnEstablishedAbsence:
    def test_a_failed_retrieval_blocks_it(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental File S1", resolved=False)],
        )
        assert outcome["classification"] != "missing_data"
        assert "retrieval_failed" in {limitation["kind"] for limitation in outcome["limitations"]}

    def test_the_retrieval_limitation_says_this_attempt(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental File S1", resolved=False)],
        )
        failed = next(limitation for limitation in outcome["limitations"] if limitation["kind"] == "retrieval_failed")
        assert "in this attempt" in failed["detail"]

    def test_it_is_still_reachable_when_every_source_was_inspected(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[_attachment("Supplemental File S1", resolved=True, role="sample_metadata")],
        )
        assert outcome["classification"] == "missing_data"

    def test_one_failure_is_one_line_not_one_per_artifact(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "no"}},
            supplements=[
                _attachment("Supplemental File S1", resolved=False),
                _attachment("Supplemental File S2", resolved=False),
                _attachment("Supplemental File S3", resolved=False),
            ],
        )
        assert len(outcome["checks_not_completed"]) == 1
        assert "Supplemental File S2" in outcome["checks_not_completed"][0]


# change_7.4 section 1.1: what failed after retrieval, each named for what it is.
_CAUSE_KINDS = (
    "access_refused",
    "resource_limit",
    "input_unreadable",
    "unsupported_processing",
    "input_unidentified",
    "sample_mapping_unresolved",
    "design_incompatible",
    "no_compatible_contrast",
)


class TestAFailureThatIsNotAnAbsenceNeverReadsAsOne:
    @pytest.mark.parametrize("kind", _CAUSE_KINDS)
    def test_each_is_a_limitation_kind(self, kind):
        from app.services.validation_completion import LIMITATION_KINDS

        assert kind in LIMITATION_KINDS

    @pytest.mark.parametrize("kind", _CAUSE_KINDS)
    def test_each_leaves_the_study_inconclusive(self, kind):
        from app.services.validation_completion import classification_for

        assert classification_for([{"kind": kind}]) == "inconclusive"

    @pytest.mark.parametrize("kind", _CAUSE_KINDS)
    def test_each_keeps_an_absence_elsewhere_from_being_concluded(self, kind):
        """7.3 section 5: `missing_data` needs an established absence, and a leg that failed for
        one of these reasons leaves the absence unestablished."""
        from app.services.validation_completion import classification_for

        assert classification_for([{"kind": kind}, {"kind": "missing_input"}]) == "inconclusive"

    def test_controlled_access_still_names_the_bucket(self):
        from app.services.validation_completion import classification_for

        assert classification_for([{"kind": "controlled_access"}, {"kind": "access_refused"}]) == "access_restricted"


# ---- change_7.4 section 1.3: acquired, usable and ready are three recorded facts ----


def _deposit_evidence(**extra):
    return {
        "deposit_inventory": {"accession": "GSE144396"},
        "deposit": {
            "files": [
                {
                    "filename": "GSE144396_RNA-Seq_NormalizedCounts.txt.gz",
                    "artifact_type": "deposited_matrix",
                    "url": "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE144nnn/GSE144396/suppl/x",
                }
            ]
        },
        **extra,
    }


_USABLE = {"usable": True, "n_columns": 8, "value_type_observed": "normalized_other", "unusable_reason": None}


def _facts(evidence, **kwargs):
    return completion_for(
        route="deposit",
        capabilities={"deposits": [_GEO_NO_MATRIX], "preprocessed_data": {"value": "yes"}},
        supplements=[],
        acquisition=evidence,
        **kwargs,
    )


class TestTheThreeInputFacts:
    def test_an_acquired_deposit_file_is_acquired_and_named_with_its_source(self):
        """Study 37 downloaded this file and its report said bioAF acquired none."""
        outcome = _facts(_deposit_evidence())
        assert outcome["input_acquired"] == "yes"
        assert "GSE144396_RNA-Seq_NormalizedCounts.txt.gz" in outcome["input_acquired_reason"]
        assert "GSE144396" in outcome["input_acquired_reason"]

    def test_usable_comes_from_the_inspection(self):
        assert _facts(_deposit_evidence(deposit_inspection=_USABLE))["input_usable"] == "yes"
        unusable = {"usable": False, "unusable_reason": "the deposited matrix appears to be transposed"}
        outcome = _facts(_deposit_evidence(deposit_inspection=unusable))
        assert outcome["input_usable"] == "no"
        assert "transposed" in outcome["input_usable_reason"]

    def test_an_input_nobody_inspected_is_not_established_as_usable(self):
        assert _facts(_deposit_evidence())["input_usable"] == "not_established"

    def test_ready_comes_from_the_recorded_readiness(self):
        readiness = {"value": "no", "reason": "the columns could not be assigned to SAMD1 KO and WT"}
        outcome = _facts(_deposit_evidence(deposit_inspection=_USABLE, analysis_readiness=readiness))
        assert outcome["ready_for_analysis"] == "no"
        assert "could not be assigned" in outcome["ready_for_analysis_reason"]

    def test_study_37_reads_as_acquired_usable_and_not_ready(self):
        readiness = {"value": "no", "reason": "no column could be matched to SAMD1 KO"}
        outcome = _facts(_deposit_evidence(deposit_inspection=_USABLE, analysis_readiness=readiness))
        assert (outcome["input_acquired"], outcome["input_usable"], outcome["ready_for_analysis"]) == (
            "yes",
            "yes",
            "no",
        )

    def test_nothing_acquired_is_not_ready(self):
        outcome = _facts({})
        assert outcome["input_acquired"] == "no"
        assert outcome["input_usable"] == "not_established"
        assert outcome["ready_for_analysis"] == "no"

    def test_fetched_raw_reads_are_acquired(self):
        outcome = completion_for(
            route="pipeline",
            capabilities={"deposits": [_GEO_NO_MATRIX], "raw_data": {"value": "yes"}},
            supplements=[],
            acquisition={},
            data_run_id=7,
            fetched_samples=12,
        )
        assert outcome["input_acquired"] == "yes"
        assert "12" in outcome["input_acquired_reason"]
