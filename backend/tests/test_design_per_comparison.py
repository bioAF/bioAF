"""plan_8_6 section 7: a design summary FOR EACH COMPARISON, not one for the paper.

The owner's review of the deployed code, 2026-09-21:

    "Design assessment remains paper-wide rather than comparison-specific. design_summary() selects
    the first matching replication, pairing, and selection statements across the paper, plus capped
    lists of other facts. It does not construct separate comparison-linked designs. This leaves the
    model to reconcile different experiments and replication structures itself. Associate design
    facts with their experiments and claims before judging adequacy."

Study 65 is the case. Its Figure 4 legend states `n = 2 biological replicates for each of 9 clones`
for the hormone assay and `n = 1 sample per ovaroid per condition` for the ovaroid panel beside it;
its Figure 5 legend states `2 replicates of human ovaroids` and `1 replicate of mouse xeno-ovaroids`
for the germ-cell counts. Reading "the first replication statement in the paper" answers E2.B about
whichever experiment the document happened to describe first, and the assessor is left to work out
which comparison each number belongs to from a flat list.

A comparison is anchored where the paper states its design: a figure or table legend, or the methods
subsection that describes one. Each anchor carries the claim it is about and the facts read THERE.
"""

import pathlib

from app.services.validation_design_summary import design_comparisons, design_facts
from app.services.validation_evidence_index import build_index

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "fulltext_jats.xml").read_text()


def _granulosa_index():
    from app.services.literature.fulltext_service import _jats_sections, _jats_to_text

    return build_index(_jats_to_text(_JATS), sections=_jats_sections(_JATS), source="europe_pmc")


def _by_anchor(index):
    return {c["anchor"]: c for c in design_comparisons(index)}


class TestEachComparisonKeepsItsOwnFacts:
    def test_the_paper_yields_one_record_per_stated_comparison(self):
        found = design_comparisons(_granulosa_index())
        assert len(found) > 1, "a paper-wide summary is one record; a per-comparison one is many"
        anchors = {c["anchor"] for c in found}
        assert "figure 4" in anchors
        assert "figure 5" in anchors

    def test_figure_4s_replication_is_read_from_figure_4(self):
        four = _by_anchor(_granulosa_index())["figure 4"]
        assert "9 clones" in " ".join(g["quote"] for g in four["group_sizes"]) or "n = 2 biological" in (
            four["replication"]["quote"] or ""
        )
        assert "1 sample per o" in " ".join(g["quote"] for g in four["group_sizes"])

    def test_figure_5s_replication_is_not_figure_4s(self):
        anchored = _by_anchor(_granulosa_index())
        five = anchored["figure 5"]
        assert "xeno-ovaroid" in " ".join(g["quote"] for g in five["group_sizes"]).lower() or "1 replicate" in " ".join(
            g["quote"] for g in five["group_sizes"]
        )
        assert five["group_sizes"] != anchored["figure 4"]["group_sizes"]

    def test_every_record_says_what_claim_it_is_about(self):
        for comparison in design_comparisons(_granulosa_index()):
            assert comparison["claim"], comparison["anchor"]

    def test_every_record_names_the_passages_its_facts_were_read_in(self):
        for comparison in design_comparisons(_granulosa_index()):
            for fact in ("replication", "pairing", "selection", "experimental_unit"):
                row = comparison[fact]
                if row.get("quote"):
                    assert row.get("id"), f"{comparison['anchor']} {fact}"


class TestAnUnknownFieldIsUnknownForThatComparison:
    def test_a_comparison_that_states_no_replication_says_so(self):
        index = build_index(
            "",
            sections={
                "index": [
                    {
                        "title": "Figure 1",
                        "kind": "legend",
                        "paragraphs": ["Growth of treated cells. Cells were compared to an untreated control."],
                    },
                    {
                        "title": "Figure 2",
                        "kind": "legend",
                        "paragraphs": [
                            "Expression after knockdown. Three biological replicates per group were "
                            "collected and compared with a wild-type control."
                        ],
                    },
                ]
            },
            source="pasted",
        )
        anchored = _by_anchor(index)
        assert "replication" in anchored["Figure 1"]["unknown"]
        assert "replication" not in anchored["Figure 2"]["unknown"]
        assert anchored["Figure 2"]["replication"]["count"] == 3


class TestTheFactsReachTheObligationPerComparison:
    def test_each_comparison_is_one_citable_design_record(self):
        rows = design_facts(_granulosa_index())
        assert rows, "the design facts still reach the obligation"
        assert all(row["carries"] == "design" for row in rows)
        anchored = [row for row in rows if row.get("comparison")]
        assert {row["comparison"] for row in anchored} >= {"figure 4", "figure 5"}

    def test_a_record_names_its_comparison_in_the_words_the_assessor_reads(self):
        rows = design_facts(_granulosa_index())
        four = next(row for row in rows if row.get("comparison") == "figure 4")
        assert "figure 4" in four["text"].lower()
        assert "1 sample per o" in four["text"]

    def test_a_long_record_is_carried_in_pieces_and_never_cut(self):
        long_quote = "Samples were compared with an untreated control. " + ("n = 3 per group. " * 200)
        index = build_index(
            "",
            sections={"index": [{"title": "Figure 9", "kind": "legend", "paragraphs": [long_quote]}]},
            source="pasted",
        )
        rows = [r for r in design_facts(index) if r.get("comparison") == "Figure 9"]
        assert len(rows) >= 1
        assert len({r["id"] for r in rows}) == len(rows), "each piece is its own citable id"

    def test_a_paper_with_no_design_statements_supplies_no_comparison_records(self):
        index = build_index(
            "",
            sections={"index": [{"title": "Methods", "kind": "methods", "paragraphs": ["Cells were cultured."]}]},
            source="pasted",
        )
        assert [r for r in design_facts(index) if r.get("comparison")] == []
