"""change_7.3 section 7: bounded passages of the paper, kept while the text is in hand.

No paper text reached reconciliation at all: `evidence["paper_text"]` was read in one place and
written nowhere. So the paper's own statement that three TE biopsies were excluded after quality
control could never correct "54 samples, post-QC". The passages are kept at read time, bounded, per
change_7.1 decision 5: the claim's source passage and a bounded digest, never the full text.
"""

import pathlib
import xml.etree.ElementTree as ET

from app.services.validation_passages import (
    MAX_PASSAGE_CHARS,
    MAX_STATEMENT_CHARS,
    MAX_STATEMENTS,
    paper_passages,
)

_GROFF = " ".join(
    ET.parse(pathlib.Path(__file__).parent / "fixtures" / "groff" / "fulltext_jats.xml").getroot().itertext()
)

_CLAIM = "The resulting data set includes 35 WE samples, 19 TE biopsies"


class TestTheClaimPassage:
    def test_the_passage_around_a_claim_carries_its_context(self):
        passages = paper_passages(_GROFF, [_CLAIM])
        passage = passages["claims"][0]["passage"]
        assert "three TE biopsies were excluded after quality control" in passage

    def test_a_claim_the_text_does_not_contain_has_no_passage(self):
        passages = paper_passages(_GROFF, ["a sentence that is nowhere in this paper"])
        assert passages["claims"][0]["passage"] is None

    def test_a_passage_is_bounded(self):
        passages = paper_passages("word " * 20000 + _CLAIM + " word" * 20000, [_CLAIM])
        assert len(passages["claims"][0]["passage"]) <= MAX_PASSAGE_CHARS


class TestExclusionStatements:
    def test_the_paper_s_exclusion_statement_is_kept(self):
        statements = paper_passages(_GROFF, [])["statements"]
        assert any("three TE biopsy samples were excluded" in s for s in statements)

    def test_a_sentence_about_excluding_genomic_regions_is_not_about_samples(self):
        statements = paper_passages(_GROFF, [])["statements"]
        assert not any("GRCh38" in s for s in statements)

    def test_they_are_bounded_in_number_and_length(self):
        text = " ".join(f"Sample {i} was excluded after quality control for low coverage." for i in range(50))
        statements = paper_passages(text, [])["statements"]
        assert len(statements) <= MAX_STATEMENTS
        assert all(len(s) <= MAX_STATEMENT_CHARS for s in statements)

    def test_nothing_is_kept_from_an_empty_text(self):
        assert paper_passages("", ["x"]) == {"claims": [{"claim_text": "x", "passage": None}], "statements": []}


class TestAStatementIsAboutTheAnalysedPopulation:
    """The deployed Groff run kept "embryos were removed only for the purpose of embryo biopsy" and
    "the removal of a single blastomere": a removal, but not from the analysis."""

    def test_handling_samples_is_not_excluding_them(self):
        text = (
            "Embryos were removed only for the purpose of embryo biopsy and were replaced in the well. "
            "A laser created a defect sufficient for the removal of a single blastomere."
        )
        assert paper_passages(text, [])["statements"] == []

    def test_removing_samples_from_the_analysis_is_kept(self):
        text = "We also removed samples expressing fewer than 5000 genes. The rest were analysed."
        assert paper_passages(text, [])["statements"] == ["We also removed samples expressing fewer than 5000 genes."]
