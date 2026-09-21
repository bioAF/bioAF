"""plan_8_6 section 3 item 4: what M2 and M4 are judged on is bioAF's own normalized reading.

The owner's review of the deployed code, 2026-09-21:

    "The deterministic methods checks still do not receive all the evidence they need. Some wiring
    exists, but the complete path is not working. Running the current cutoff reader against study
    65's indexed methods produced zero cutoff statements, despite the held paragraph explicitly
    stating the GO fold-change and adjusted-P thresholds. Fix normalization and analysis-scoped
    propagation, then test the final M2/M4 outcomes, not merely that methods paragraphs were stored."

Two separate defects behind that zero:

1. **Normalization.** Europe PMC flattens `P_adj` to `p adj`, and the significance reader knew only
   `padj` and `adjusted P value`. Study 65's `log 2 fc >3, p adj < 0.05` normalized to nothing.
2. **Analysis-scoped propagation.** `methods_statements` keeps only sentences that DEFINE a
   differential test, which is plan_8_2's inheritance rule and must not change: a GO-enrichment
   input threshold is not the cutoff a differential claim inherits. But M4 asks whether the decision
   criteria applied to the analysis output are specified and unambiguous, and that sentence is
   exactly its evidence. It is read, scoped to the operation it governs, and carried - never
   inherited into a claim.

M2's evidence is the same shape: the reference and annotation the methods name, normalized through
`validation_reference`, so "Ensembl GRCh38 v96" reaches the obligation as a build and a release
rather than as a sentence nobody parsed.
"""

import pathlib

import pytest

from app.services.validation_evidence_index import build_index, methods_paragraphs
from app.services.validation_methods_cutoffs import analysis_statements, record as record_methods
from app.services.validation_reference import reference_statements

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "fulltext_jats.xml").read_text()


def _granulosa_index():
    from app.services.literature.fulltext_service import _jats_sections, _jats_to_text

    return build_index(_jats_to_text(_JATS), sections=_jats_sections(_JATS), source="europe_pmc")


class TestTheSubscriptSpellingIsNormalized:
    def test_a_flattened_p_adj_is_read_as_an_adjusted_significance_cutoff(self):
        found = analysis_statements(
            [
                "PantherDB was used to calculate gene ontology enrichment for significantly "
                "upregulated (log 2 fc >3, p adj < 0.05) genes for each sample relative to hiPSCs."
            ]
        )
        assert found, "study 65's own sentence states two cutoffs and read as none"
        cutoffs = {(c["kind"], c["operator"], c["value"]) for c in found[0]["cutoffs"]}
        assert ("padj", "<", 0.05) in cutoffs
        assert ("abs_log2fc", ">", 3.0) in cutoffs

    @pytest.mark.parametrize(
        "sentence",
        [
            "Genes with p adj < 0.05 were called differentially expressed.",
            "Genes with P adj. < 0.05 were called differentially expressed.",
            "Genes with q adj < 0.01 were called differentially expressed.",
            "Genes with padj < 0.05 were called differentially expressed.",
            "Genes with an adjusted P value below 0.05 were called differentially expressed.",
        ],
    )
    def test_every_spelling_of_an_adjusted_cutoff_reaches_the_differential_reader(self, sentence):
        found = record_methods([sentence], source="europe_pmc")
        assert found["statements"], sentence
        assert any(c["kind"] == "padj" for c in found["statements"][0]["cutoffs"]), sentence


class TestACutoffIsScopedToTheAnalysisItGoverns:
    def test_the_go_threshold_is_read_with_the_operation_it_governs(self):
        found = analysis_statements(methods_paragraphs(_granulosa_index()))
        enrichment = [s for s in found if "enrichment" in s["analysis"]]
        assert enrichment, "the GO-enrichment threshold reached no reader"
        assert "log 2 fc >3" in enrichment[0]["quote"]

    def test_it_is_not_inherited_into_a_claim_as_a_differential_cutoff(self):
        """plan_8_2 owner decision 2 is untouched: a GO-enrichment input threshold is not the cutoff
        a differential claim inherits, and reading it here must not make it one."""
        paragraphs = [
            "PantherDB was used to calculate gene ontology enrichment for significantly upregulated "
            "(log 2 fc >3, p adj < 0.05) genes."
        ]
        assert analysis_statements(paragraphs)
        assert record_methods(paragraphs, source="europe_pmc")["statements"] == []

    def test_a_sentence_with_no_operation_named_is_not_scoped_to_one(self):
        assert analysis_statements(["The threshold was 0.05."]) == []


class TestTheReferenceTheMethodsNameIsNormalized:
    def test_study_65s_transcriptome_build_and_release_are_read(self):
        found = reference_statements(methods_paragraphs(_granulosa_index()))
        assert found, "the paper names Ensembl GRCh38 v96 and it reached no reader"
        assert any(s["build"] == "GRCh38" for s in found)
        assert any("v96" in s["quote"] or (s.get("annotation") or {}).get("release") == 96 for s in found)

    def test_a_species_name_alone_is_not_a_reference(self):
        assert reference_statements(["Reads were aligned to the human transcriptome."]) == []


class TestTheNormalizedEvidenceReachesM2AndM4:
    def _rows(self):
        from app.services.validation_documentary_review import carried_evidence

        return carried_evidence(evidence={"paper_index": _granulosa_index()}, plan={})["rows"]

    def test_the_normalized_cutoffs_are_carried_as_evidence(self):
        from app.services.validation_evidence_packets import CUTOFF

        rows = [r for r in self._rows() if r.get("kind") == CUTOFF]
        assert rows
        assert any("log 2 fc >3" in r["text"] for r in rows)
        assert any("padj" in r["text"] and "0.05" in r["text"] for r in rows)

    def test_the_normalized_reference_is_carried_as_evidence(self):
        from app.services.validation_evidence_packets import REFERENCE

        rows = [r for r in self._rows() if r.get("kind") == REFERENCE]
        assert rows
        assert any("GRCh38" in r["text"] for r in rows)

    def test_m4_is_given_the_cutoffs_and_m2_the_reference(self):
        from app.services.validation_documentary_review import packets_for

        packets = packets_for(
            evidence={"paper_index": _granulosa_index()}, plan={}, leaves=("M2.A", "M2.B", "M4.A", "M4.B")
        )
        m4 = " ".join(p["text"] for p in packets["M4.B"]["passages"])
        m2 = " ".join(p["text"] for p in packets["M2.A"]["passages"])
        assert "log 2 fc >3" in m4
        assert "GRCh38" in m2

    def test_a_computational_obligation_is_not_given_the_reference_record(self):
        from app.services.validation_documentary_review import packets_for
        from app.services.validation_evidence_packets import REFERENCE

        packets = packets_for(evidence={"paper_index": _granulosa_index()}, plan={}, leaves=("S2.A",))
        assert not [p for p in packets["S2.A"]["passages"] if str(p.get("id") or "").startswith(REFERENCE)]


class TestTheFinalM2AndM4Outcomes:
    """Acceptance gate 1: the M2/M4 OUTCOMES, not that methods paragraphs were stored.

    Both are deterministic checks reading the extraction's per-experiment reference and per-contrast
    thresholds. Where the extraction recorded neither, they reported "bioAF's read recorded no
    annotation release for this experiment" and "the comparison's significance cutoff is not stated"
    about a paper whose methods state `Ensembl GRCh38 v96` and `log 2 fc >3, p adj < 0.05`. That is
    bioAF's reading reported as the paper's silence.
    """

    def _assess(self, *, experiments, contrasts, index=None):
        from app.services.validation_rubric_evidence import assess_evidence

        return assess_evidence(
            plan={
                "reported_experiments": experiments,
                "differential_design": {"contrasts": contrasts},
            },
            evidence={"paper_index": index if index is not None else _granulosa_index()},
            claims=[],
        )

    def test_the_annotation_release_the_methods_state_reaches_m2_b(self):
        found = self._assess(
            experiments=[{"id": "E1", "reference": {"assembly": {"stated": "GRCh38", "status": "usable"}}}],
            contrasts=[],
        )
        assert found["M2.B"]["outcome"] == "verified", found["M2.B"]["rationale"]
        assert "96" in found["M2.B"]["rationale"]

    def test_two_different_releases_in_the_methods_are_not_resolved_by_picking_one(self):
        two = build_index(
            "",
            sections={
                "index": [
                    {
                        "title": "Methods",
                        "kind": "methods",
                        "paragraphs": [
                            "Reads were aligned to GRCh38 with Ensembl v96.",
                            "Counts were summarised over Ensembl release 102 annotation.",
                        ],
                    }
                ]
            },
            source="pasted",
        )
        found = self._assess(
            experiments=[{"id": "E1", "reference": {"assembly": {"stated": "GRCh38", "status": "usable"}}}],
            contrasts=[],
            index=two,
        )
        assert found["M2.B"]["outcome"] == "undetermined"
        assert "96" in found["M2.B"]["rationale"] and "102" in found["M2.B"]["rationale"]

    def test_m4_a_says_which_analysis_the_stated_criteria_govern(self):
        found = self._assess(
            experiments=[{"id": "E1"}],
            contrasts=[{"name": "FOXL2 overexpression vs hiPSC control"}],
        )
        assert found["M4.A"]["outcome"] == "undetermined"
        assert "enrichment" in found["M4.A"]["rationale"], found["M4.A"]["rationale"]
        assert "log 2 fc >3" in found["M4.A"]["rationale"]

    def test_criteria_stated_for_the_differential_test_itself_establish_m4_a(self):
        stated = build_index(
            "",
            sections={
                "index": [
                    {
                        "title": "Methods",
                        "kind": "methods",
                        "paragraphs": [
                            "Differential expression was tested with DESeq2 and genes with p adj < 0.05 "
                            "and an absolute log2 fold change above 1 were reported."
                        ],
                    }
                ]
            },
            source="pasted",
        )
        found = self._assess(
            experiments=[{"id": "E1"}],
            contrasts=[{"name": "treated vs control"}],
            index=stated,
        )
        assert found["M4.A"]["outcome"] == "verified", found["M4.A"]["rationale"]
        assert "padj" in found["M4.A"]["rationale"]

    def test_a_paper_stating_no_criteria_at_all_is_unchanged(self):
        bare = build_index(
            "",
            sections={"index": [{"title": "Methods", "kind": "methods", "paragraphs": ["Cells were cultured."]}]},
            source="pasted",
        )
        found = self._assess(experiments=[{"id": "E1"}], contrasts=[{"name": "treated vs control"}], index=bare)
        assert found["M4.A"]["outcome"] == "undetermined"
        assert "not stated" in found["M4.A"]["rationale"]
