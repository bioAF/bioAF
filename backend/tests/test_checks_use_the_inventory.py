"""change_7.1 section 7: a check must not contradict evidence sitting beside it.

Study 32 recorded `sample_data_matches_paper: "no deposited files were listed to compare against"`
in the same evidence bundle that held Supplemental File S1, resolved, classified as sample
metadata, with 54 rows and a Sex column. The paper's own sample table was right there.

`species_matches` said "the deposit declares no organism to compare against" for an EGA deposit
whose organism bioAF never had a source for. Never having looked and having looked and found
nothing are different statements, and only one of them is about the deposit.
"""

import pytest

from app.services.validation_precompute_checks import check_sample_data, check_species

_S1 = {
    "label": "Supplemental File S1",
    "filename": "s1.txt",
    "role": "sample_metadata",
    "resolved": True,
    "row_count": 54,
    "columns": ["ProcessingID", "Sampletype", "Sex"],
}


class TestTheSampleCheckUsesThePapersOwnTable:
    def test_a_metadata_supplement_answers_when_the_deposit_lists_nothing(self):
        result = check_sample_data(paper_sample_count=54, entries=[], supplements=[_S1])
        assert result["verdict"] == "ok"

    def test_the_detail_names_the_supplement_it_used(self):
        result = check_sample_data(paper_sample_count=54, entries=[], supplements=[_S1])
        assert "Supplemental File S1" in result["detail"]

    def test_a_row_count_that_disagrees_is_a_mismatch(self):
        result = check_sample_data(paper_sample_count=99, entries=[], supplements=[_S1])
        assert result["verdict"] == "mismatch"
        assert "54" in result["detail"] and "99" in result["detail"]

    def test_it_never_says_no_files_were_listed_when_files_are_known(self):
        """The acceptance line: a known file listing cannot yield a no-files-listed explanation."""
        result = check_sample_data(paper_sample_count=54, entries=[], supplements=[_S1])
        assert "no deposited files were listed" not in result["detail"]

    def test_an_unresolved_supplement_cannot_answer_it(self):
        """A reference nobody fetched holds no row count, and guessing from its name would be
        inventing evidence."""
        result = check_sample_data(
            paper_sample_count=54,
            entries=[],
            supplements=[{"label": "Supplemental File S1", "role": "unknown", "resolved": False}],
        )
        assert result["verdict"] == "unknown"

    def test_nothing_anywhere_is_still_unknown(self):
        result = check_sample_data(paper_sample_count=54, entries=[], supplements=[])
        assert result["verdict"] == "unknown"

    def test_a_deposit_listing_still_wins_when_there_is_one(self):
        """The deposit is the better evidence about the deposit. The supplement is the fallback."""

        class _Entry:
            level = "series"
            classification = "matrix_counts"
            gsm = None

        result = check_sample_data(paper_sample_count=54, entries=[_Entry()], supplements=[_S1])
        assert result["verdict"] == "ok"
        assert "series-level" in result["detail"]


class TestTheSpeciesCheckSaysWhichItIs:
    def test_never_having_a_source_is_not_the_deposit_declaring_none(self):
        result = check_species(plan_organism="Homo sapiens", deposit_organisms=None, organism_source=False)
        assert result["verdict"] == "unknown"
        assert "declares no organism" not in result["detail"]

    def test_a_deposit_that_was_read_and_named_nothing_still_says_so(self):
        result = check_species(plan_organism="Homo sapiens", deposit_organisms=[], organism_source=True)
        assert result["verdict"] == "unknown"
        assert "declares no organism" in result["detail"]

    @pytest.mark.parametrize("organisms", [["Homo sapiens"], ["homo sapiens", "Mus musculus"]])
    def test_a_match_is_unaffected(self, organisms):
        result = check_species(plan_organism="Homo sapiens", deposit_organisms=organisms, organism_source=True)
        assert result["verdict"] == "ok"

    def test_a_real_mismatch_still_blocks(self):
        result = check_species(plan_organism="Homo sapiens", deposit_organisms=["Mus musculus"], organism_source=True)
        assert result["verdict"] == "mismatch"
        assert result["blocking"] is True
