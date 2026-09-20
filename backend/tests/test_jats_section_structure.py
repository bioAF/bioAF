"""plan_8_6 section 3: prefer the article's own section structure over headings guessed from prose.

`_jats_sections` kept the methods paragraphs and the figure and table legends and threw the rest of
the document's structure away. A paper's results, its discussion, its limitations and its data
availability statement then reached the index only as headings inferred from flattened prose, and a
methods subsection title ("Bulk RNA-seq analysis") was lost entirely, so nothing could say which
part of the methods a passage came from.
"""

from app.services.literature.fulltext_service import _jats_sections

_XML = """<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <body>
    <sec sec-type="intro"><title>Introduction</title>
      <p>Granulosa cells are the somatic cells of the ovarian follicle.</p>
    </sec>
    <sec sec-type="results"><title>Results</title>
      <p>Directed differentiation produced granulosa-like cells.</p>
      <fig><label>Figure 4</label><caption><p>One sample per clone and condition.</p></caption></fig>
    </sec>
    <sec sec-type="materials|methods"><title>Materials and methods</title>
      <sec><title>Cell culture</title>
        <p>hiPSCs were maintained on Matrigel in mTeSR Plus.</p>
      </sec>
      <sec><title>Bulk RNA-seq analysis</title>
        <p>Reads were trimmed with Trim Galore and aligned with STAR 2.7.9a.</p>
      </sec>
    </sec>
    <sec><title>Discussion</title>
      <p>Only selected high-performing clones were carried forward.</p>
    </sec>
  </body>
  <back>
    <sec><title>Data availability</title><p>Code is at github.com/programmablebio/granulosa.</p></sec>
  </back>
</article>
"""


class TestTheWholeStructureIsCarried:
    def test_the_section_index_names_every_section(self):
        found = _jats_sections(_XML)
        titles = [s["title"].lower() for s in found["index"]]
        assert "introduction" in titles
        assert "results" in titles
        assert "discussion" in titles
        assert "data availability" in titles

    def test_a_methods_subsection_keeps_its_own_title(self):
        found = _jats_sections(_XML)
        entry = next(s for s in found["index"] if "STAR 2.7.9a" in " ".join(s["paragraphs"]))
        assert entry["title"] == "Bulk RNA-seq analysis"
        assert entry["kind"] == "methods"

    def test_a_section_is_classified_by_kind(self):
        kinds = {s["title"].lower(): s["kind"] for s in _jats_sections(_XML)["index"]}
        assert kinds["introduction"] == "introduction"
        assert kinds["results"] == "results"
        assert kinds["discussion"] == "discussion"
        assert kinds["cell culture"] == "methods"
        assert kinds["data availability"] == "availability"

    def test_a_legend_is_carried_with_its_label(self):
        entry = next(s for s in _jats_sections(_XML)["index"] if s["kind"] == "legend")
        assert "figure 4" in entry["title"].lower()
        assert "One sample per clone" in " ".join(entry["paragraphs"])


class TestTheExistingConsumersAreUnchanged:
    def test_the_methods_paragraphs_are_still_there(self):
        found = _jats_sections(_XML)
        assert any("Trim Galore" in p for p in found["methods"])
        assert any("Matrigel" in p for p in found["methods"])

    def test_the_captions_are_still_addressable(self):
        assert "One sample per clone" in _jats_sections(_XML)["captions"]["figure 4"]

    def test_unparseable_markup_has_no_sections(self):
        found = _jats_sections("not xml at all")
        assert found["methods"] == [] and found["captions"] == {} and found["index"] == []


class TestTheIndexReadsIt:
    def test_the_index_prefers_the_articles_own_structure(self):
        from app.services.validation_evidence_index import build_index

        sections = _jats_sections(_XML)
        index = build_index("irrelevant flattened text", sections=sections, source="europe_pmc")
        passage = next(p for p in index["passages"] if "STAR 2.7.9a" in p["text"])
        assert passage["kind"] == "methods"
        assert passage["certain"] is True
        assert "Bulk RNA-seq analysis" in passage["section"]

    def test_the_discussion_reaches_the_index_as_a_certain_section(self):
        from app.services.validation_evidence_index import build_index

        index = build_index("", sections=_jats_sections(_XML), source="europe_pmc")
        passage = next(p for p in index["passages"] if "high-performing clones" in p["text"])
        assert passage["kind"] == "discussion"
        assert passage["certain"] is True
