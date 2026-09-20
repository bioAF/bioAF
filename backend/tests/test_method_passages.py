"""plan_8_5 section 3.6: the paper's own methods sentences, kept while the text is in hand.

A judgment about an experimental procedure or a statistical design has to rest on what the paper
says, and bioAF kept none of it: the claim passages and the exclusion statements were all that
survived a read. So an assessor would have been judging from bioAF's summary of a paper rather than
from the paper.

Bounded, like everything else kept here: sentences that describe a procedure, capped in number and
in length, so a long paper costs what a short one does.
"""

from app.services.validation_passages import MAX_METHOD_STATEMENTS, method_statements

_TEXT = (
    "We recruited 54 donors. "
    "Embryos were cultured to day 5 and biopsied. "
    "RNA was extracted with TRIzol and libraries were prepared using Smart-seq2. "
    "Reads were aligned to hg19 with STAR and quantified with RSEM. "
    "Differential expression was tested with DESeq2 using a Wald test, and P values were adjusted "
    "by the Benjamini-Hochberg procedure. "
    "We thank the donors for their participation. "
    "The results show a clear separation between XX and XY embryos."
)


class TestWhatIsKept:
    def test_a_procedure_sentence_is_kept(self):
        kept = method_statements(_TEXT)
        assert any("Smart-seq2" in s for s in kept)
        assert any("STAR" in s for s in kept)

    def test_a_statistical_sentence_is_kept(self):
        assert any("Benjamini-Hochberg" in s for s in method_statements(_TEXT))

    def test_an_acknowledgement_is_not_a_method(self):
        assert not any("thank the donors" in s for s in method_statements(_TEXT))

    def test_a_result_sentence_is_not_a_method(self):
        assert not any("clear separation" in s for s in method_statements(_TEXT))

    def test_the_number_of_sentences_is_bounded(self):
        long_text = " ".join(f"Samples were sequenced on instrument {i} using a kit." for i in range(200))
        assert len(method_statements(long_text)) <= MAX_METHOD_STATEMENTS

    def test_nothing_in_nothing_out(self):
        assert method_statements("") == []


class TestItTravelsWithTheOtherPassages:
    def test_the_record_carries_the_methods_beside_the_claims_and_the_exclusions(self):
        from app.services.validation_passages import paper_passages

        record = paper_passages(_TEXT, ["We found a clear separation"])
        assert record["methods"]
        assert "claims" in record and "statements" in record
