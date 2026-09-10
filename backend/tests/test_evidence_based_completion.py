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
        """The deposit route needs a processed matrix. EGA published none. That is a missing input,
        and calling it access_restricted describes the wrong obstacle."""
        outcome = completion_for(
            route="deposit",
            capabilities={"deposits": [_EGA_DEPOSIT], "preprocessed_data": {"value": "no"}},
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
        assert outcome["processed_results_available"] is True
        assert outcome["reproduction_input_available"] is False

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
        kinds = {limitation["kind"] for limitation in outcome["limitations"]}
        assert {"unsupported_acquisition", "missing_input"} <= kinds

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
