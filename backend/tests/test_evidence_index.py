"""plan_8_6 section 3: the paper's text, indexed by section, so an obligation can be given what answers it.

`validation_passages.method_statements` kept at most 40 sentences that matched a keyword regex, in
document order, each cut at 400 characters. On study 65 the regex matched narrative prose in the
introduction and the results ("differentiating", "sorted", "cloning") and exhausted the budget near
the start of the cell-culture methods, before the sequencing and computational procedures. M1, M3
and M5 were then judged on evidence that did not contain the methods they are about.

The Methods section is COVERED, not sampled. The index carries the whole available text with each
passage's section, its kind and where in the document it sits, once per source revision, and the
per-obligation packet selects from it.
"""

from app.services.validation_evidence_index import INDEX_VERSION, build_index, methods_paragraphs

_METHODS_PARAGRAPHS = [
    (
        "Cell culture. hiPSCs were maintained on Matrigel-coated plates in mTeSR Plus medium and "
        "passaged with 0.5 mM EDTA every four days. Doxycycline was added at 1 ug/mL to induce the "
        "transgene."
    ),
    (
        "Bulk RNA sequencing. Total RNA was extracted with the RNeasy Mini Kit and libraries were "
        "prepared with the Illumina Stranded mRNA kit. Libraries were sequenced on a NovaSeq 6000 to "
        "a depth of 30 million paired-end reads per sample."
    ),
    (
        "Bulk RNA-seq analysis. Reads were trimmed with Trim Galore 0.6.7, aligned to GRCh38 with "
        "STAR 2.7.9a and quantified with RSEM 1.3.3. Differential expression was tested with DESeq2 "
        "1.34.0. Genes with an absolute log2 fold change greater than 3 and an adjusted P value "
        "below 0.05 were used as input to GO enrichment."
    ),
    (
        "Single-cell RNA sequencing. Libraries were prepared with the 10x Chromium Single Cell 3' v3 "
        "kit. Cells with fewer than 500 detected genes or more than 15% mitochondrial reads were "
        "removed, and doublets were called with Scrublet."
    ),
]

_SECTIONS = {
    "methods": list(_METHODS_PARAGRAPHS),
    "captions": {
        "figure 4": "Figure 4. Expression of granulosa markers. One sample per clone and condition.",
        "figure 5": "Figure 5. Comparison with mouse granulosa cells. A single mouse replicate was used.",
    },
}

_FULL_TEXT = (
    "Introduction. Ovarian granulosa cells are the somatic cells of the follicle. "
    + " ".join(f"Prior work established point {i} about follicle biology." for i in range(120))
    + " Results. Directed differentiation produced granulosa-like cells. "
    + " ".join(_METHODS_PARAGRAPHS)
    + " Discussion. Only selected high-performing clones were carried forward, and one granulosa-like "
    "line was used for the single-cell experiments."
)


class TestTheWholeMethodsSectionIsCarried:
    def test_the_detailed_computational_methods_are_indexed(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        text = " ".join(p["text"] for p in index["passages"])
        assert "Trim Galore" in text
        assert "STAR 2.7.9a" in text
        assert "DESeq2" in text
        assert "Scrublet" in text

    def test_a_long_irrelevant_introduction_does_not_exhaust_the_index(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        methods = [p for p in index["passages"] if p["kind"] == "methods"]
        assert any("RSEM" in p["text"] for p in methods)

    def test_no_passage_is_silently_cut_mid_sentence(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        for passage in index["passages"]:
            assert passage["text"].strip()
            assert not passage["text"].endswith(("with", "the", "and", "of", "to"))

    def test_a_long_paragraph_is_split_rather_than_truncated(self):
        long_paragraph = " ".join(f"Step {i} was performed with tool {i} at setting {i}." for i in range(200))
        index = build_index(long_paragraph, sections={"methods": [long_paragraph], "captions": {}}, source="pasted")
        carried = " ".join(p["text"] for p in index["passages"] if p["kind"] == "methods")
        assert "Step 199" in carried


class TestEachPassageKnowsWhereItCameFrom:
    def test_a_passage_carries_its_section_kind_and_location(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        passage = next(p for p in index["passages"] if "STAR 2.7.9a" in p["text"])
        assert passage["kind"] == "methods"
        assert passage["section"]
        assert passage["location"]
        assert passage["id"]

    def test_a_figure_legend_is_indexed_as_a_legend(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        legends = [p for p in index["passages"] if p["kind"] == "legend"]
        assert any("One sample per clone" in p["text"] for p in legends)
        assert any("figure 4" in (p["section"] or "").lower() for p in legends)

    def test_a_passage_id_is_stable_for_the_same_source(self):
        first = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        second = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        assert [p["id"] for p in first["passages"]] == [p["id"] for p in second["passages"]]

    def test_a_paragraph_the_markup_placed_is_not_indexed_again_from_the_flat_text(self):
        """One paragraph in two sections would let a packet spend its budget twice on one method."""
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        assert len([p for p in index["passages"] if "STAR 2.7.9a" in p["text"]]) == 1

    def test_the_index_records_the_source_it_was_built_from(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        assert index["source"] == "europe_pmc"
        assert index["source_sha256"]
        assert index["index_version"] == INDEX_VERSION


class TestSectionsWithoutMarkup:
    def test_headings_in_flat_text_are_recognised_and_marked_uncertain(self):
        flat = (
            "Introduction Granulosa cells matter. "
            "Materials and Methods Reads were aligned to GRCh38 with STAR and quantified with RSEM. "
            "Results We observed clear separation. "
            "Discussion The clones were selected."
        )
        index = build_index(flat, sections=None, source="library")
        methods = [p for p in index["passages"] if p["kind"] == "methods"]
        assert methods, "a methods heading in flat text is found"
        assert any("STAR" in p["text"] for p in methods)
        assert all(p["certain"] is False for p in methods)

    def test_a_reordered_paper_does_not_lose_its_methods(self):
        reordered = (
            "Methods Reads were aligned to GRCh38 with STAR and quantified with RSEM. "
            "Introduction Granulosa cells matter. "
            "Results We observed clear separation."
        )
        index = build_index(reordered, sections=None, source="library")
        assert any("RSEM" in p["text"] for p in index["passages"] if p["kind"] == "methods")

    def test_text_with_no_headings_at_all_is_still_indexed(self):
        index = build_index("Reads were aligned with STAR and quantified with RSEM.", sections=None, source="pasted")
        assert index["passages"]
        assert any("STAR" in p["text"] for p in index["passages"])

    def test_nothing_in_nothing_out(self):
        index = build_index("", sections=None, source="pasted")
        assert index["passages"] == []
        assert index["sections"] == []


class TestWhatTheDeterministicConsumersGet:
    def test_the_methods_paragraphs_are_available_for_the_cutoff_readers(self):
        """Section 3 item 4: M2 and M4 read methods paragraphs, and a paper with no JATS structure
        supplied them none."""
        flat = "Methods Genes with an adjusted P value below 0.05 were considered differentially expressed."
        index = build_index(flat, sections=None, source="library")
        paragraphs = methods_paragraphs(index)
        assert paragraphs
        assert any("differentially expressed" in p for p in paragraphs)

    def test_the_xml_methods_paragraphs_are_preferred_where_they_exist(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        paragraphs = methods_paragraphs(index)
        assert any("Trim Galore" in p for p in paragraphs)


class TestTheIndexRecordsItsOwnCoverage:
    def test_it_says_which_sections_it_holds(self):
        index = build_index(_FULL_TEXT, sections=_SECTIONS, source="europe_pmc")
        kinds = {s["kind"] for s in index["sections"]}
        assert "methods" in kinds
        assert "legend" in kinds

    def test_a_section_bioaf_could_not_place_is_recorded_as_other_rather_than_dropped(self):
        index = build_index("Some prose with no heading at all about anything.", sections=None, source="pasted")
        assert index["passages"]
        assert {p["kind"] for p in index["passages"]} == {"other"}
