"""plan_8_1 section 3.4: a deposited matrix is a candidate until its value type is established.

"3 of 61 deposited files could serve as a reproduction input" counted normalized matrices, and DESeq2 on a
normalized matrix is invalid. A deposited matrix is an ELIGIBLE input only when its value type is
established and a valid test exists for that type; until then it is a candidate. A deposited result table
is never an input: it is the authors' answer.
"""

from app.services.deposit_selection import input_eligibility


class TestEligibility:
    def test_a_normalized_matrix_without_an_established_value_type_is_a_candidate(self):
        assert input_eligibility("matrix_normalized", None) == "candidate"
        assert input_eligibility("matrix_normalized", "unknown") == "candidate"

    def test_a_matrix_whose_measured_value_type_has_a_valid_test_is_eligible(self):
        assert input_eligibility("matrix_normalized", "tpm") == "eligible"
        assert input_eligibility("matrix_counts", "counts") == "eligible"

    def test_a_result_table_is_never_a_reanalysis_input(self):
        assert input_eligibility("de_table", "counts") == "never"
        assert input_eligibility("da_table", None) == "never"

    def test_a_coverage_track_is_not_an_input(self):
        assert input_eligibility("coverage", None) == "never"


class TestTheDiscoveryRowSaysCandidate:
    def test_the_listing_counts_candidate_reproduction_inputs(self):
        from app.services import archive_discovery

        assert (
            archive_discovery.candidate_inputs_words(3, 61)
            == "3 of 61 deposited file(s) are candidate reproduction inputs"
        )
