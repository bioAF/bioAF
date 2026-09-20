"""plan_8_6 section 3 item 4: the deterministic readers get the same source-backed methods.

`validation_methods_cutoffs` was fed `_jats_sections["methods"]`, which only a Europe PMC route
fills. A study whose text came from the Literature Library or from a paste supplied it nothing, so
M2 and M4 were silently exempt on every such paper: not "the paper states no cutoff" but "bioAF
never gave the reader the paper's methods".

They read the evidence index instead, which every route fills. No second scorer is invented: this is
the same normalization path, given the methods it was always supposed to have.
"""

import pathlib

from app.services.validation_evidence_index import build_index, methods_paragraphs
from app.services.validation_methods_cutoffs import record as record_methods

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "fulltext_jats.xml").read_text()


class TestAPaperWithNoMarkupStillHasMethods:
    def test_a_pasted_paper_supplies_its_cutoff_sentence(self):
        flat = (
            "Introduction Granulosa cells matter. "
            "Methods Genes with an adjusted P value below 0.05 and an absolute log2 fold change "
            "greater than 1 were considered differentially expressed. "
            "Results We found 1204 genes."
        )
        index = build_index(flat, sections=None, source="library")
        found = record_methods(methods_paragraphs(index), source="library")
        assert found["statements"], "the paper's own cutoff sentence reached the reader"
        assert any("differentially expressed" in s["quote"] for s in found["statements"])

    def test_the_route_that_always_worked_still_does(self):
        paragraphs = ["Genes with padj < 0.05 were considered differentially expressed."]
        index = build_index(
            "",
            sections={"index": [{"title": "Methods", "kind": "methods", "paragraphs": paragraphs}]},
            source="europe_pmc",
        )
        assert record_methods(methods_paragraphs(index), source="europe_pmc")["statements"]

    def test_a_paper_with_no_methods_at_all_states_no_cutoff(self):
        index = build_index("Some prose.", sections=None, source="pasted")
        assert record_methods(methods_paragraphs(index), source="pasted")["statements"] == []


class TestStudy65sOwnMethods:
    def _index(self):
        from app.services.literature.fulltext_service import _jats_sections, _jats_to_text

        return build_index(_jats_to_text(_JATS), sections=_jats_sections(_JATS), source="europe_pmc")

    def test_the_whole_methods_section_reaches_the_cutoff_reader(self):
        paragraphs = methods_paragraphs(self._index())
        carried = " ".join(paragraphs)
        assert "kallisto" in carried
        assert "log 2 fc >3" in carried
        assert "Ensembl GRCh38 v96" in carried

    def test_the_reader_is_given_more_than_the_first_forty_sentences(self):
        from app.services.validation_passages import method_statements
        from app.services.literature.fulltext_service import _jats_to_text

        old = " ".join(method_statements(_jats_to_text(_JATS)))
        new = " ".join(methods_paragraphs(self._index()))
        assert "log 2 fc >3" not in old
        assert "log 2 fc >3" in new
