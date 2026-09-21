"""plan_8_6 section 7: controls credited without examining the design.

Study 65 returned verified for BOTH halves of E2. Its rationales mention comparators and assert
their appropriateness, and do not assess replication, clone selection or group sizes for the
specific inferences. The paper describes selected high-performing clones, one granulosa-like line
for the single-cell experiments, one mouse comparator replicate in Figure 5, and one sample per
clone and condition in Figure 4B; its single-cell methods report two samples per time point, each
pooling six ovaroids, so "one line" must not be read as "one sample".

E2.B is answered from the DESIGN FACTS: the experimental unit, the replicate kind and count,
pairing or blocking, comparator relevance, and how clones or lines were selected. Where those are
not in evidence it is untested, and a small study or a selected clone is not automatically invalid:
what must be distinguished is the inference the design supports from the one it does not.
"""

import pathlib

from app.services.validation_design_summary import design_facts, design_summary
from app.services.validation_evidence_index import build_index

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "fulltext_jats.xml").read_text()


def _granulosa_index():
    from app.services.literature.fulltext_service import _jats_sections, _jats_to_text

    return build_index(_jats_to_text(_JATS), sections=_jats_sections(_JATS), source="europe_pmc")


class TestWhatTheSummaryReads:
    def test_the_replicate_kind_and_count_are_read_from_the_paper(self):
        found = design_summary(_granulosa_index())
        assert found["replication"]["kind"] == "biological"
        assert found["replication"]["quote"]
        assert "Two biological replicates" in found["replication"]["quote"]

    def test_the_selection_of_clones_or_lines_is_read(self):
        found = design_summary(_granulosa_index())
        assert found["selection"]["quote"]
        assert "clone" in found["selection"]["quote"].lower() or "line" in found["selection"]["quote"].lower()

    def test_the_group_sizes_the_paper_states_are_read(self):
        """Under the COMPARISON that states them. The paper-wide reading takes the first few group
        sizes in the document and the single-cell ones are not among them, which is the defect
        `design_comparisons` exists for: see `test_design_per_comparison`."""
        from app.services.validation_design_summary import design_comparisons

        single_cell = next(
            c for c in design_comparisons(_granulosa_index()) if c["anchor"] == "Single-cell RNA sequencing"
        )
        quotes = " ".join(str(g.get("quote") or "") for g in single_cell["group_sizes"])
        assert "2 samples per time point" in quotes or "6 ovaroids per sample" in quotes

    def test_the_comparators_the_paper_names_are_read(self):
        found = design_summary(_granulosa_index())
        assert found["comparators"]["named"]

    def test_a_field_the_paper_does_not_state_is_explicit(self):
        thin = build_index(
            "",
            sections={"index": [{"title": "Methods", "kind": "methods", "paragraphs": ["Cells were cultured."]}]},
            source="pasted",
        )
        found = design_summary(thin)
        assert "replication" in found["unknown"]
        assert found["replication"]["kind"] == "unknown"

    def test_every_field_is_named_as_known_or_unknown(self):
        found = design_summary(_granulosa_index())
        for field in ("experimental_unit", "replication", "pairing", "comparators", "selection", "group_sizes"):
            assert field in found

    def test_nothing_in_nothing_out(self):
        found = design_summary(build_index("", sections=None, source="pasted"))
        assert found["unknown"]
        assert found["facts"] == []


class TestTheFactsReachTheObligation:
    def test_each_fact_is_a_citable_passage_of_its_own(self):
        facts = design_facts(_granulosa_index())
        assert facts
        assert all(f["id"].startswith("design:") for f in facts)
        assert all(f["kind"] == "design" for f in facts)
        assert all(f["carries"] == "design" for f in facts)

    def test_the_unknown_fields_travel_beside_the_known_ones(self):
        partial = build_index(
            "",
            sections={
                "index": [
                    {
                        "title": "Methods",
                        "kind": "methods",
                        "paragraphs": ["Two biological replicates were collected for each sample."],
                    }
                ]
            },
            source="pasted",
        )
        facts = design_facts(partial)
        assert any("Replication" in f["text"] for f in facts)
        assert any("not stated" in f["text"].lower() for f in facts)

    def test_a_paper_bioaf_read_no_design_facts_from_supplies_none(self):
        """A note saying bioAF found nothing is not evidence. An obligation with no design facts is
        untested, not one judged on a sentence about bioAF."""
        thin = build_index(
            "",
            sections={"index": [{"title": "Methods", "kind": "methods", "paragraphs": ["Cells were cultured."]}]},
            source="pasted",
        )
        assert design_facts(thin) == []

    def test_e2_is_given_the_design_facts(self):
        from app.services.validation_evidence_packets import packet_for

        index = _granulosa_index()
        packet = packet_for("E2.B", index=index, extras=design_facts(index))
        assert any(p["id"].startswith("design:") for p in packet["passages"])

    def test_a_computational_obligation_is_not_given_them(self):
        from app.services.validation_evidence_packets import packet_for

        index = _granulosa_index()
        packet = packet_for("M1.B", index=index, extras=design_facts(index))
        assert not any(p["id"].startswith("design:") for p in packet["passages"])


class TestTheComparatorAloneCannotEstablishTheDesign:
    _PASSAGES = [
        {"id": "p1", "source": "the paper's results", "text": "KGN and COV434 cells were used as comparators."},
        {
            "id": "design:1",
            "source": "the design of the comparisons this paper claims",
            "text": "Replication: biological, 2 per sample. Quote: Two biological replicates were collected.",
            "carries": "design",
        },
    ]

    def _answer(self, citations, outcome="met"):
        return {
            "outcome": outcome,
            "rationale": "the comparators are appropriate for the comparison claimed",
            "citations": list(citations),
            "confidence": 0.8,
        }

    def test_a_met_e2_b_citing_only_comparators_establishes_nothing(self):
        from app.services.validation_judgment import judgment_from
        from app.services.validation_rubric_v3 import UNDETERMINED

        found = judgment_from("E2.B", self._answer(["p1"]), passages=self._PASSAGES)
        assert found["outcome"] == UNDETERMINED
        assert "design" in found["rationale"]

    def test_a_met_e2_b_that_rests_on_a_design_fact_is_accepted(self):
        from app.services.validation_judgment import judgment_from
        from app.services.validation_rubric_v3 import VERIFIED

        found = judgment_from("E2.B", self._answer(["p1", "design:1"]), passages=self._PASSAGES)
        assert found["outcome"] == VERIFIED

    def test_an_unmet_e2_b_does_not_need_a_design_fact_to_say_one_is_missing(self):
        from app.services.validation_judgment import judgment_from
        from app.services.validation_rubric_v3 import FAILED

        found = judgment_from(
            "E2.B",
            {
                "outcome": "unmet",
                "rationale": "a single line is compared with a single mouse replicate, so the difference "
                "claimed cannot be separated from line-to-line variation",
                "citations": ["p1"],
                "impact": "the comparison does not support the claim",
            },
            passages=self._PASSAGES,
        )
        assert found["outcome"] == FAILED

    def test_e2_a_is_not_held_to_the_same_requirement(self):
        """E2.A asks whether the controls and design choices are DESCRIBED. E2.B adds the reasoned
        evaluation of their adequacy, and only that needs a design fact to rest on."""
        from app.services.validation_judgment import judgment_from
        from app.services.validation_rubric_v3 import VERIFIED

        found = judgment_from("E2.A", self._answer(["p1"]), passages=self._PASSAGES)
        assert found["outcome"] == VERIFIED

    def test_the_request_says_a_comparator_is_not_a_design(self):
        from app.services.validation_judgment import build_request

        payload = build_request("E2.B", passages=self._PASSAGES)["payload"].lower()
        assert "replicat" in payload
        assert "presence of" in payload or "mere presence" in payload
